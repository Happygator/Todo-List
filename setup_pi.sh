#!/bin/bash

# Exit on error
set -e

echo "Starting setup for Todo-List Bot on Raspberry Pi..."

# 1. Update package list and install python3-venv
echo "Updating package list and installing python3-venv..."
sudo apt-get update
sudo apt-get install -y python3-venv

# 2. Create virtual environment
echo "Creating virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
else
    echo "Virtual environment already exists."
fi

# 3. Activate and install requirements
echo "activating virtual environment and installing requirements..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "========================================"
echo "Setup complete!"
echo "To run the bot, use:"
echo "source .venv/bin/activate"
echo "python bot.py"
echo "========================================"
