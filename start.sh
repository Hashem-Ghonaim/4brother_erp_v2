#!/bin/bash

# ======================================================
#      Starting MyERP Fashion System...
# ======================================================

echo "======================================================"
echo "      Starting MyERP Fashion System..."
echo "======================================================"

# 1. الذهاب لمجلد المشروع
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# ================================================
# 2. لو venv موجود اصلاً استخدمه مباشرة
# ================================================
if [ -f "$DIR/venv/bin/python" ]; then
    echo "[OK] Virtual environment found."
    VENV_PYTHON="$DIR/venv/bin/python"
else
    # ================================================
    # 3. venv مش موجود - ابحث عن Python لإنشائه
    # ================================================
    echo "[INFO] No venv found. Searching for Python..."
    
    if command -v python3 &> /dev/null; then
        PYTHON_EXE="python3"
    elif command -v python &> /dev/null; then
        PYTHON_EXE="python"
    else
        echo ""
        echo "[ERROR] Python not found on this machine!"
        echo "Please install Python 3 (e.g. sudo apt install python3 python3-venv)"
        read -p "Press Enter to exit..."
        exit 1
    fi

    echo "[OK] Python found: $PYTHON_EXE"
    echo "[INFO] Creating virtual environment..."
    "$PYTHON_EXE" -m venv "$DIR/venv" --without-pip
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        read -p "Press Enter to exit..."
        exit 1
    fi
    
    echo "[INFO] Installing pip..."
    curl -sS https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    "$DIR/venv/bin/python" get-pip.py > /dev/null 2>&1
    rm get-pip.py
    
    VENV_PYTHON="$DIR/venv/bin/python"
fi

# ================================================
# 4. تثبيت/تحديث المكتبات
# ================================================
echo "Checking requirements..."
"$VENV_PYTHON" -m pip install -r requirements.txt

# ================================================
# 5. فتح Firewall (اختياري في لينكس)
# ================================================
# sudo ufw allow 5000/tcp > /dev/null 2>&1

# ================================================
# 6. جيب الـ IP المحلي
# ================================================
LOCAL_IP=$(hostname -I | awk '{print $1}')
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="unknown"
fi

# ================================================
# 7. فتح المتصفح وتشغيل السيرفر
# ================================================
# Wait for 2 seconds in the background and then open browser
(sleep 2 && xdg-open "http://127.0.0.1:5000" > /dev/null 2>&1) &

echo ""
echo "======================================================"
echo " [READY] MyERP is running!"
echo ""
echo " This device  >  http://127.0.0.1:5000"
echo " Network      >  http://$LOCAL_IP:5000"
echo ""
echo " Press Ctrl+C to stop"
echo "======================================================"
echo ""

"$VENV_PYTHON" app.py

echo ""
echo "[SERVER STOPPED]"
read -p "Press Enter to exit..."
