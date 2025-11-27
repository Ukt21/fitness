from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from models import SessionLocal, Message, User

app = FastAPI()
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

@app.get("/", response_class=HTMLResponse)
def index():
    db = SessionLocal()
    items = db.query(Message, User).join(User, User.id == Message.user_id).all()
    html = "<h1>Сообщения пользователей</h1>"
    for msg, user in items:
        html += "<div style='padding:10px;margin:10px;background:#eee'>"
        html += f"<b>{user.first_name or user.username}</b><br>"
        if msg.type == "text":
            html += msg.text
        elif msg.type == "photo":
            html += f"<img src='/uploads/{msg.file_path}' width='250'>"
        elif msg.type == "voice":
            html += f"<audio controls src='/uploads/{msg.file_path}'></audio>"
        html += "</div>"
    return html
