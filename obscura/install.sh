#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# System dep for Kokoro phonemization
sudo apt-get install -y espeak-ng

# Create venv if it doesn't exist
if [ ! -d venv ]; then
    python3 -m venv venv
fi

venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "Install complete. Run with: ./run.sh"

cat > run.sh << 'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
exec venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
EOF
chmod +x run.sh
