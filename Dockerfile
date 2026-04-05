FROM python:3.12-slim

# System deps for psycopg2-binary, matplotlib, reportlab
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    libfreetype6-dev \
    libjpeg62-turbo-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY core/ core/
COPY agents/ agents/
COPY onboarding/ onboarding/
COPY applicant/ applicant/
COPY ui/ ui/
COPY shipathon_JMD/ shipathon_JMD/
COPY service.py main.py setup.py ./

# Create runtime directories
RUN mkdir -p /app/generated /app/resumes

# Entrypoint: run onboarding setup, then start server
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["/app/entrypoint.sh"]
