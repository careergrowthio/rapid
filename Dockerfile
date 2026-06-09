# Rapid — one cycle of the pipeline per container start. A host scheduler
# (Railway/Render cron) runs this hourly; the container exits when the cycle ends.
FROM python:3.11-slim

WORKDIR /app

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + skills + email template (loaded relative to the module)
COPY rapid_agent.py rapid_matching_skill.md rapid_resume_tailoring_skill.md \
     rapid_match_email_template.md ./

# Secrets come from the platform's env vars, NEVER baked into the image.
# Run one cycle and exit (the scheduler re-invokes hourly).
CMD ["python", "rapid_agent.py"]
