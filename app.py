import json
import re
from urllib.parse import quote

import streamlit as st
from google import genai
from google.genai import types
from twilio.rest import Client as TwilioClient

from prompts import (
    QUICK_ACTION_PROMPTS,
    SUMMARY_REQUEST_PROMPT,
    SYSTEM_PROMPT,
    WELCOME_MESSAGE_TEMPLATE,
)


MODEL_NAME = "gemini-3.8-flash"
FALLBACK_MODEL_NAME = "gemini-3.6-flash"
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png"}
TWILIO_SECRET_NAMES = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_FROM",
    "TWILIO_CONTENT_SID",
)

st.set_page_config(page_title="StudySnap", page_icon="\U0001F4DA")


# -----------------------------
# Secrets and cached API clients
# -----------------------------
try:
    APP_SECRETS = st.secrets
except Exception:
    APP_SECRETS = {}
GEMINI_API_KEY = str(APP_SECRETS.get("GEMINI_API_KEY", "")).strip()
TWILIO_SETTINGS = {
    name: str(APP_SECRETS.get(name, "")).strip()
    for name in TWILIO_SECRET_NAMES
}


@st.cache_resource
def get_gemini_client(api_key):
    return genai.Client(api_key=api_key)


@st.cache_resource
def get_twilio_client(account_sid, auth_token):
    return TwilioClient(account_sid, auth_token)


if not GEMINI_API_KEY:
    st.error(
        "StudySnap needs GEMINI_API_KEY. Add it to "
        ".streamlit/secrets.toml, then restart the app."
    )
    st.stop()

gemini_client = get_gemini_client(GEMINI_API_KEY)


# -----------------------------
# UI and study helpers
# -----------------------------
def render_message(message):
    with st.chat_message(message["role"]):
        if message["kind"] == "text":
            st.write(message["content"])
        elif message["kind"] == "image":
            st.image(message["content"])
            file_name = message.get("file_name", "uploaded image")
            mime_type = message.get("mime_type", "image")
            st.caption(f"{file_name} ({mime_type})")


def add_message(role, kind, content, **metadata):
    message = {"role": role, "kind": kind, "content": content, **metadata}
    st.session_state.messages.append(message)
    render_message(message)


def gemini_error_kind(error):
    status_code = getattr(error, "code", None)
    status = str(getattr(error, "status", "")).upper()
    details = str(error).lower()
    if status_code == 503 or "unavailable" in status or "high demand" in details:
        return "busy"
    if (
        status_code == 429
        or "resource_exhausted" in status
        or "quota" in details
        or "rate limit" in details
    ):
        return "quota"
    if status_code in (401, 403) or "unauthorized" in details:
        return "credentials"
    if status_code == 404 or "not found" in details:
        return "model"
    if status_code == 400:
        return "request"
    if "winerror 10013" in details or "connection" in details or "timeout" in details:
        return "network"
    return "service"


def fallback_contents(extra_parts=None):
    contents = []
    for message in st.session_state.messages:
        role = "model" if message["role"] == "assistant" else "user"
        if message["kind"] == "image":
            part = types.Part.from_bytes(
                data=message["content"],
                mime_type=message.get("mime_type", "image/jpeg"),
            )
        else:
            part = types.Part(text=str(message["content"]))

        if contents and contents[-1].role == role:
            contents[-1].parts.append(part)
        else:
            contents.append(types.Content(role=role, parts=[part]))

    if extra_parts:
        contents.append(types.Content(role="user", parts=extra_parts))
    return contents


def ask_gemini(parts, append_request_on_fallback=False):
    st.session_state.gemini_error = None
    if st.session_state.get("gemini_quota_exhausted"):
        st.session_state.gemini_error = "quota"
        return None
    if st.session_state.get("use_fallback_model"):
        try:
            response = gemini_client.models.generate_content(
                model=FALLBACK_MODEL_NAME,
                contents=fallback_contents(parts if append_request_on_fallback else None),
                config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
            )
            if response.text:
                return response.text
            st.session_state.gemini_error = "empty"
            return None
        except Exception as error:
            st.session_state.gemini_error = gemini_error_kind(error)
            if st.session_state.gemini_error == "quota":
                st.session_state.gemini_quota_exhausted = True
            return None

    try:
        response = st.session_state.chat.send_message(parts)
        if not response.text:
            st.session_state.gemini_error = "empty"
            return None
        return response.text
    except Exception as error:
        if gemini_error_kind(error) == "busy":
            st.session_state.use_fallback_model = True
            try:
                response = gemini_client.models.generate_content(
                    model=FALLBACK_MODEL_NAME,
                    contents=fallback_contents(
                        parts if append_request_on_fallback else None
                    ),
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT
                    ),
                )
                if response.text:
                    return response.text
                st.session_state.gemini_error = "empty"
                return None
            except Exception as fallback_error:
                st.session_state.gemini_error = gemini_error_kind(fallback_error)
                if st.session_state.gemini_error == "quota":
                    st.session_state.gemini_quota_exhausted = True
                return None
        st.session_state.gemini_error = gemini_error_kind(error)
        if st.session_state.gemini_error == "quota":
            st.session_state.gemini_quota_exhausted = True
        return None


def generate_study_summary():
    """Summarize the conversation, falling back to a local digest on quota errors."""
    st.session_state.gemini_error = None
    st.session_state.summary_is_local = False
    if st.session_state.get("gemini_quota_exhausted"):
        st.session_state.gemini_error = "quota"
        st.session_state.summary_is_local = True
        return build_local_study_summary()
    try:
        response = gemini_client.models.generate_content(
            model=FALLBACK_MODEL_NAME,
            contents=fallback_contents(
                [types.Part(text=SUMMARY_REQUEST_PROMPT)]
            ),
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        summary = (response.text or "").strip()
        if summary:
            return summary
        st.session_state.gemini_error = "empty"
        st.session_state.summary_is_local = True
        return build_local_study_summary()
    except Exception as error:
        st.session_state.gemini_error = gemini_error_kind(error)
        st.session_state.summary_is_local = True
        if st.session_state.gemini_error == "quota":
            st.session_state.gemini_quota_exhausted = True
        return build_local_study_summary()


def build_local_study_summary():
    """Create a WhatsApp-ready digest without making another API request."""
    topics = []
    explanations = []
    for message in st.session_state.messages:
        if message["role"] == "user":
            if message["kind"] == "image":
                topic = f"Uploaded study image: {message.get('file_name', 'image')}"
            else:
                topic = str(message["content"]).strip()
                if topic.lower().startswith("please help me with:"):
                    topic = topic.split(":", 1)[-1].strip()
                if topic in QUICK_ACTION_PROMPTS:
                    continue
            if topic:
                topics.append(topic)
        elif message["kind"] == "text":
            text = str(message["content"]).strip()
            if (
                text
                and "I'm StudySnap" not in text
                and not text.startswith("Gemini could not")
            ):
                explanations.append(text)

    lines = [f"StudySnap study session for {st.session_state.name}", ""]
    if topics:
        lines.extend(["Topics and questions:"])
        lines.extend(f"• {topic}" for topic in topics[-5:])
    if explanations:
        lines.extend(["", "Key explanations:"])
        for explanation in explanations[-3:]:
            snippet = explanation[:420].rsplit(" ", 1)[0]
            if len(explanation) > 420:
                snippet += "…"
            lines.append(f"• {snippet}")
    else:
        lines.extend(
            [
                "",
                "No tutor explanation was saved before the Gemini quota was "
                "reached. Revisit the topics above when Gemini is available.",
            ]
        )
    return "\n".join(lines)[:2200]


def show_gemini_error(default_message):
    error_kind = st.session_state.get("gemini_error")
    if error_kind == "busy":
        st.info(
            f"{default_message} Gemini is temporarily busy. Please retry in a moment."
        )
    elif error_kind == "quota":
        st.info(
            "Gemini AI answers are temporarily unavailable because this API "
            "project has reached its quota. StudySnap is running normally; "
            "new AI explanations will work again when the quota resets or is "
            "increased. Saved conversation summaries and WhatsApp drafts still work."
        )
    elif error_kind == "credentials":
        st.error(
            f"{default_message} Check that GEMINI_API_KEY in "
            ".streamlit/secrets.toml is valid and enabled."
        )
    elif error_kind == "network":
        st.error(
            f"{default_message} StudySnap could not reach Gemini. Check your "
            "internet connection and try again."
        )
    elif error_kind == "model":
        st.error(
            f"{default_message} The configured Gemini model is unavailable "
            "for this API key."
        )
    elif error_kind == "request":
        st.error(
            f"{default_message} Gemini rejected the request. Try a shorter "
            "study session or remove an unreadable image."
        )
    elif error_kind == "empty":
        st.error(
            f"{default_message} Gemini returned an empty response. Please "
            "try again."
        )
    else:
        st.error(
            f"{default_message} Gemini could not complete the request. "
            "Check API usage limits and try again."
        )


def normalize_whatsapp_number(number):
    number = number.strip()
    if number.lower().startswith("whatsapp:"):
        number = number.split(":", 1)[1].strip()

    digits = "".join(character for character in number if character.isdigit())
    if digits.startswith("00") and not number.startswith("+"):
        digits = digits[2:]

    e164_number = "+" + digits
    if not re.fullmatch(r"\+[1-9]\d{7,14}", e164_number):
        raise ValueError("Enter the full WhatsApp number with its country code.")

    return e164_number


def whatsapp_draft_url(number, message):
    digits = normalize_whatsapp_number(number).lstrip("+")
    return f"https://wa.me/{digits}?text={quote(message, safe='')}"


def send_whatsapp_summary(student_name, number, summary):
    missing = [name for name in TWILIO_SECRET_NAMES if not TWILIO_SETTINGS[name]]
    if missing:
        return "not_configured", None

    try:
        client = get_twilio_client(
            TWILIO_SETTINGS["TWILIO_ACCOUNT_SID"],
            TWILIO_SETTINGS["TWILIO_AUTH_TOKEN"],
        )
        content_variables = json.dumps(
            {"1": student_name, "2": summary},
            ensure_ascii=False,
        )
        message = client.messages.create(
            from_=TWILIO_SETTINGS["TWILIO_WHATSAPP_FROM"],
            to=f"whatsapp:{normalize_whatsapp_number(number)}",
            content_sid=TWILIO_SETTINGS["TWILIO_CONTENT_SID"],
            content_variables=content_variables,
        )
        return "sent", message.sid
    except Exception as error:
        details = str(error).lower()
        if "trial accounts have limited parameter access" in details:
            return "trial_limited", None
        return "error", getattr(error, "code", None)


def answer_study_prompt(prompt, display_text, label):
    add_message("user", "text", display_text)
    with st.spinner("StudySnap is thinking..."):
        answer = ask_gemini([prompt], append_request_on_fallback=True)

    if answer is None:
        answer = build_local_quick_tool_response(label)
        if st.session_state.get("gemini_error") == "quota":
            st.info(
                "Gemini's quota is exhausted. This response uses the saved "
                "study conversation instead."
            )
        else:
            st.info(
                "Gemini could not respond. This response uses the saved "
                "study conversation instead."
            )
    add_message("assistant", "text", answer)


def build_local_quick_tool_response(label):
    digest = build_local_study_summary()
    if label == "Summarize":
        return digest
    return (
        f"Gemini could not run {label.lower()} right now.\n\n"
        "Here is the digest of the material already saved in this session "
        "instead:\n\n"
        f"{digest}"
    )


def offline_study_answer(question):
    """Provide a small built-in explanation for a common study topic."""
    normalized = question.lower()
    if "newton" not in normalized or "law" not in normalized:
        return None

    return (
        "## Newton's three laws of motion\n\n"
        "1. **Inertia:** An object stays at rest or keeps moving at a constant "
        "velocity unless a net external force acts on it. A seat belt helps "
        "stop your body when a car brakes.\n\n"
        "2. **Force and acceleration:** The net force on an object is its mass "
        "times its acceleration: **F_net = m × a**. For the same force, a "
        "lighter object accelerates more.\n\n"
        "3. **Action and reaction:** Forces come in equal and opposite pairs "
        "that act on different objects. When you push on a wall, the wall "
        "pushes back on you.\n\n"
        "This is a built-in study note because Gemini is temporarily "
        "unavailable. Send a specific problem when AI service is available "
        "for a step-by-step solution."
    )


# -----------------------------
# Onboarding
# -----------------------------
if "onboarded" not in st.session_state:
    st.title("\U0001F4DA StudySnap")
    st.caption("Your AI study companion")

    with st.form("onboarding_form"):
        name = st.text_input("Student name")
        whatsapp_number = st.text_input(
            "WhatsApp number (with country code)",
            placeholder="+91XXXXXXXXXX",
            help="Use the full international number, for example +91XXXXXXXXXX.",
        )
        submitted = st.form_submit_button("Start studying")

    if submitted:
        if not name.strip() or not whatsapp_number.strip():
            st.warning("Enter your name and WhatsApp number to continue.")
        else:
            try:
                normalize_whatsapp_number(whatsapp_number)
                chat = gemini_client.chats.create(
                    model=MODEL_NAME,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT
                    ),
                )
                st.session_state.name = name.strip()
                st.session_state.whatsapp_number = whatsapp_number.strip()
                st.session_state.chat = chat
                st.session_state.messages = []
                st.session_state.onboarded = True
                st.rerun()
            except ValueError as error:
                st.error(str(error))
            except Exception:
                st.error("StudySnap could not connect to Gemini. Check your API key and connection.")

    st.stop()


# -----------------------------
# Header and study-summary action
# -----------------------------
header_col, button_col = st.columns([5, 2], vertical_alignment="center")
with header_col:
    st.title("\U0001F4DA StudySnap")
    st.caption("Your AI study companion")
with button_col:
    if st.button("Send study summary", use_container_width=True):
        has_study_content = any(
            message["role"] == "user" for message in st.session_state.messages
        )
        if not has_study_content:
            st.info("Ask a study question or upload study material first.")
        else:
            with st.spinner("Preparing your study-session summary..."):
                summary = generate_study_summary()
            if summary is None:
                show_gemini_error("Gemini could not prepare the summary.")
            else:
                st.session_state.study_summary = summary

if "study_summary" in st.session_state:
    summary = st.session_state.study_summary
    st.markdown("#### Study-session summary")
    if st.session_state.get("summary_is_local"):
        st.info(
            "Gemini's quota is exhausted, so this summary was prepared from "
            "your saved conversation."
        )
    st.write(summary)
    send_col, draft_col = st.columns(2)
    with send_col:
        if st.button("Send study summary to WhatsApp", use_container_width=True):
            status, _ = send_whatsapp_summary(
                st.session_state.name,
                st.session_state.whatsapp_number,
                summary,
            )
            if status == "sent":
                st.success("Study summary sent to WhatsApp.")
            elif status == "trial_limited":
                st.info(
                    "Your Twilio trial cannot send custom summary variables. "
                    "Open a WhatsApp draft and tap Send instead."
                )
            elif status == "not_configured":
                st.info(
                    "Twilio is not fully configured. Open a WhatsApp draft "
                    "and tap Send."
                )
            else:
                st.warning(
                    "Twilio could not send the summary. Open a WhatsApp draft "
                    "and tap Send."
                )
    with draft_col:
        try:
            st.link_button(
                "Open WhatsApp draft",
                whatsapp_draft_url(st.session_state.whatsapp_number, summary),
                use_container_width=True,
            )
        except ValueError as error:
            st.error(str(error))
    st.caption("Review the message in WhatsApp, then tap Send.")

st.caption(
    f"Studying as {st.session_state.name} - "
    f"WhatsApp summary for {st.session_state.whatsapp_number}"
)


# -----------------------------
# Conversation history and quick study actions
# -----------------------------
if not st.session_state.messages:
    add_message(
        "assistant",
        "text",
        WELCOME_MESSAGE_TEMPLATE.format(name=st.session_state.name),
    )
else:
    for message in st.session_state.messages:
        render_message(message)

st.markdown("#### Quick study tools")
action_items = list(QUICK_ACTION_PROMPTS.items())
for row_start in range(0, len(action_items), 4):
    action_columns = st.columns(4)
    for column, (label, prompt) in zip(
        action_columns, action_items[row_start:row_start + 4]
    ):
        with column:
            if st.button(label, use_container_width=True, key=f"action_{label}"):
                st.session_state.pop("study_summary", None)
                st.session_state.pending_study_prompt = (label, prompt)

pending = st.session_state.pop("pending_study_prompt", None)
if pending:
    label, prompt = pending
    answer_study_prompt(prompt, f"Please help me with: {label}.", label)


# -----------------------------
# Chat input with image support
# -----------------------------
user_input = st.chat_input(
    "Ask a study question or attach notes, a page, or a diagram",
    accept_file=True,
    file_type=["jpg", "jpeg", "png"],
)

if user_input:
    st.session_state.pop("study_summary", None)
    photo = user_input.files[0] if user_input.files else None
    text = (user_input.text or "").strip()
    parts = []

    if photo is not None:
        if photo.type not in SUPPORTED_IMAGE_TYPES:
            st.error("Upload a JPG, JPEG, or PNG image.")
            st.stop()
        photo_bytes = photo.getvalue()
        add_message(
            "user",
            "image",
            photo_bytes,
            file_name=photo.name,
            mime_type=photo.type,
        )
        parts.append(
            types.Part.from_bytes(data=photo_bytes, mime_type=photo.type)
        )

    if text:
        add_message("user", "text", text)
        parts.append(text)
    elif photo is not None:
        parts.append(
            "Explain the important information in this study image. "
            "Ask me a clarifying question if any part is hard to read."
        )

    if not parts:
        st.warning("Type a question or attach a study image first.")
    else:
        with st.spinner("StudySnap is thinking..."):
            answer = ask_gemini(parts)
        if answer is None:
            offline_answer = offline_study_answer(text)
            if offline_answer:
                st.info(
                    "Gemini is temporarily busy. Showing a built-in study "
                    "note for this topic."
                )
                add_message("assistant", "text", offline_answer)
            else:
                show_gemini_error("Gemini could not answer right now.")
        else:
            add_message("assistant", "text", answer)
