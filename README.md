# StudySnap — AI Study Assistant

StudySnap is a Streamlit study companion powered by Gemini. Ask questions,
share a photo of study material, get step-by-step explanations, and create
study aids. Prepare a WhatsApp summary at the end of a session.

## Features

- Student onboarding with name and WhatsApp number.
- Text chat with follow-up questions in the same Gemini conversation.
- JPG, JPEG, and PNG uploads for notes, book pages, diagrams, and problems.
- Summaries, flashcards, quizzes, key points, and worked explanations.
- Study-session summary with a WhatsApp draft link.
- Local conversation-based summary fallback when Gemini quota is exhausted.
- Optional Twilio sending when the account and approved WhatsApp template
  support the configured parameters.

## Project files

```text
app.py
prompts.py
requirements.txt
README.md
.gitignore
.streamlit/
  secrets.toml.example
```

## Setup on Windows PowerShell

From the project folder:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If the virtual environment already exists, activate it and continue from the
pip upgrade command.

## Configure API keys

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, then add
your own credentials. The real `secrets.toml` is excluded by `.gitignore`.

### Gemini

Create a Gemini API key in Google AI Studio and set `GEMINI_API_KEY` for AI
tutoring. Without the key, StudySnap still starts in offline mode with saved-chat
summaries and a small set of built-in study notes.

### Optional Twilio WhatsApp sending

Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, and
`TWILIO_CONTENT_SID` only if you want the app to try sending through Twilio.
Create an approved WhatsApp Content Template that uses `{{1}}` for the
student's name and `{{2}}` for the study summary, and use its Content SID.
Never put credentials in source code or share them in chat.

Twilio trial accounts restrict which message parameters can be sent, and may
reject custom template variables. If that happens, StudySnap opens a WhatsApp
click-to-chat draft containing the full summary; review it and tap **Send**.
This manual draft works without upgrading Twilio. Twilio trial recipients may
also need to be verified in the Twilio console.

If Gemini's API quota is exhausted, the study-summary button builds a concise
digest locally from saved chat messages so you can still prepare a WhatsApp
draft. Gemini-powered tutoring resumes when the API quota is available again.

## Run locally

```powershell
streamlit run app.py
```

Open the local URL printed by Streamlit, usually `http://localhost:8501`.

## Deploy on Streamlit Community Cloud

1. Push this project to a GitHub repository. Keep `.streamlit/secrets.toml`
   private; `.gitignore` excludes it.
2. Sign in at [share.streamlit.io](https://share.streamlit.io/) with GitHub and
   choose **Create app**.
3. Select the repository and branch, set the app file to `app.py`, and choose
   Python 3.13 in advanced settings.
4. Add `GEMINI_API_KEY` in the app's **Secrets** settings to enable AI
   tutoring. Without it the app still runs in offline mode. Do not paste the
   local secrets file into GitHub. Add Twilio keys only if automatic Twilio
   sending is configured; WhatsApp draft links need no Twilio credentials.
5. Deploy. Community Cloud installs packages listed in `requirements.txt`.

See Streamlit's guides for [deployment](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
and [secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).
