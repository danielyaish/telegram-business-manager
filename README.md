# 💼 Telegram Business Manager

A full-featured business management system built as a Telegram Mini App. Track revenue, expenses, clients, and projects through a beautiful visual dashboard — right inside Telegram.

---

## 🏗️ Architecture

| Component | Technology | Description |
|-----------|-----------|-------------|
| 🐍 Backend | Python + FastAPI | API server + Telegram bot |
| 🗄️ Database | SQLAlchemy + SQLite | Full persistence & audit log |
| 🌐 Frontend | HTML + Chart.js | Telegram Mini App |
| ☁️ Hosting | Netlify (free) | Frontend hosting |
| 🔒 SSL | Nginx + Certbot | HTTPS for API |
| 🤖 AI | Anthropic Claude | Smart tips & business advisor |

---

## ✨ Features

### 📊 Dashboard
- 6 KPI cards showing USD and ILS simultaneously
- Monthly profit goal with animated progress bar
- 🏆 Top 3 ROI projects with explanations
- 4 interactive charts — bar, line trend, 2 doughnut charts
- 💡 AI-powered tips that auto-rotate every 2 minutes
- 🌙 Auto dark mode (19:00–07:00), manual toggle available
- Personalized greeting from your Telegram profile

### 🤖 Projects
- 8-step wizard with fun questions for adding projects
- Partial payment tracking with visual progress bar
- Automatic ROI calculation per project
- ✏️ Edit and 🗑️ delete with confirmation
- 🎉 Confetti animation on full payment
- Status tracking: Active / Done / Paused

### 👤 Clients
- Full client profile with linked projects
- Outstanding balance tracking
- Edit client details anytime

### 🔄 Recurring Costs
- Completely independent from projects
- Monthly and yearly cost totals
- Supports USD / ILS and multiple frequencies

### 📈 Reports
- Weekly / Monthly / Quarterly breakdowns
- Bots vs Groups ROI comparison
- Outstanding balance summary

### ⚙️ Automations
- 🔔 Automatic payment reminders
- ⏰ Deadline alerts 3 days in advance
- 📅 Automatic end-of-month summary
- 💾 Daily database backup

---

## 🚀 Setup

### Requirements
- Python 3.12+
- Linux server with a static IP
- Netlify account (free)
- Telegram bot token from @BotFather
- Anthropic API key (optional, for AI tips)

### 1. 📦 Install dependencies
```bash
pip3 install python-telegram-bot[job-queue]==21.5 fastapi==0.111.0 uvicorn==0.30.1 sqlalchemy==2.0.31 requests==2.32.3 pydantic==2.7.4
```

### 2. ⚙️ Configure `bot.py`
```python
BOT_TOKEN = "your_bot_token_from_botfather"
ALLOWED_USER_ID = 123456789        # get from @userinfobot
WEBAPP_URL = "https://your-app.netlify.app"
ANTHROPIC_API_KEY = "sk-ant-..."   # from console.anthropic.com
```

### 3. ☁️ Deploy `index.html` to Netlify
- Go to **app.netlify.com/drop**
- Drag and drop the file
- Copy your HTTPS URL

### 4. 🔗 Update API URL in `index.html`
```javascript
const API = 'https://api.your-domain.com';
```

### 5. 🔒 Configure Nginx with SSL
```bash
sudo apt install nginx certbot python3-certbot-nginx -y
sudo nano /etc/nginx/sites-available/api
```
```nginx
server {
    listen 80;
    server_name api.your-domain.com;
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        add_header 'Access-Control-Allow-Origin' '*' always;
        add_header 'Access-Control-Allow-Methods' 'GET, POST, PUT, DELETE, OPTIONS' always;
        add_header 'Access-Control-Allow-Headers' 'Content-Type' always;
        if ($request_method = OPTIONS) { return 204; }
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/api /etc/nginx/sites-enabled/
sudo certbot --nginx -d api.your-domain.com
```

### 6. ▶️ Run the bot
```bash
screen -S businessbot
python3 bot.py
# Ctrl+A then D to detach
```

### 7. 📱 Register Mini App in BotFather
```
/newapp → select your bot → paste your Netlify URL
```

---

## 📁 Project Structure

```
business_manager/
├── 🐍 bot.py           # Backend — FastAPI + Telegram bot
├── 🌐 index.html       # Frontend — Mini App
├── 📋 requirements.txt
├── 🗄️ business.db      # Auto-created on first run
└── 💾 backups/         # Auto daily backups
```

---

## 🔐 Security

- Access restricted to a single `ALLOWED_USER_ID`
- Full HTTPS on all communication
- Anthropic API key stored server-side only
- All actions logged with timestamps

---

## 🛠️ Stack

```
python-telegram-bot  21.5
FastAPI              0.111.0
SQLAlchemy           2.0.31
Chart.js             4.4.1
Anthropic Claude     Sonnet
Nginx + Certbot      SSL
Netlify              Frontend hosting
```

---

## 📄 License

MIT — free to use and modify.
