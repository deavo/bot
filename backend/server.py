from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks, Request
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime
import httpx
import asyncio
import json
from bson import ObjectId


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# --------------- Models ---------------
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str


class Quote(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str
    text: str
    author: Optional[str] = None
    source: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class QuoteCreate(BaseModel):
    category: str
    text: str
    author: Optional[str] = None
    source: Optional[str] = None

class QuotesResponse(BaseModel):
    items: List[Quote]
    page: int
    limit: int
    total: int
    total_pages: int


class WebhookSetRequest(BaseModel):
    public_url: str
    secret_token: Optional[str] = None

class WebhookStatusResponse(BaseModel):
    is_active: bool
    webhook_url: Optional[str]
    last_update: Optional[datetime]
    message: str


# --------------- Utilities ---------------
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None

# In-memory webhook config (persist later if needed)
webhook_config: Dict[str, Any] = {
    "url": None,
    "secret_token": None,
    "is_active": False,
    "last_update": None,
}


def sanitize_mongo_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    # Remove ObjectId and map to our Quote model
    doc = dict(doc)
    if '_id' in doc:
        doc.pop('_id', None)
    return doc


# --------------- Routes ---------------
@api_router.get("/")
async def root():
    return {"message": "Hello World"}


# Status checks
@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**sanitize_mongo_doc(status_check)) for status_check in status_checks]


# Quotes: categories list
@api_router.get("/quotes/categories")
async def get_categories():
    pipeline = [
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]
    groups = await db.quotes.aggregate(pipeline).to_list(length=1000)
    categories = [{"name": g.get("_id"), "count": g.get("count", 0)} for g in groups]
    return {"categories": categories}


# Quotes: paginated list
@api_router.get("/quotes", response_model=QuotesResponse)
async def list_quotes(category: Optional[str] = None, page: int = 1, limit: int = 10):
    if page < 1:
        page = 1
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100

    query: Dict[str, Any] = {}
    if category:
        query["category"] = category

    total = await db.quotes.count_documents(query)
    skip = (page - 1) * limit
    cursor = db.quotes.find(query).sort("created_at", -1).skip(skip).limit(limit)
    docs = [sanitize_mongo_doc(d) async for d in cursor]
    items = [Quote(**d) for d in docs]

    total_pages = (total + limit - 1) // limit if total else 0
    return QuotesResponse(items=items, page=page, limit=limit, total=total, total_pages=total_pages)


# Quotes: import from local JSONL file (streaming, scalable to millions)
@api_router.post("/quotes/import")
async def import_quotes(file_path: Optional[str] = None, overwrite: bool = False):
    path = Path(file_path) if file_path else ROOT_DIR / "data" / "quotes.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    if overwrite:
        await db.quotes.delete_many({})

    inserted = 0
    batch = []
    batch_size = 1000

    # Stream-read JSON Lines to support huge datasets
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                # Ensure id
                if 'id' not in data:
                    data['id'] = str(uuid.uuid4())
                if 'created_at' not in data:
                    data['created_at'] = datetime.utcnow().isoformat()
                # Validate minimal fields
                if not data.get('text') or not data.get('category'):
                    continue
                batch.append(data)
                if len(batch) >= batch_size:
                    await db.quotes.insert_many(batch)
                    inserted += len(batch)
                    batch = []
            except Exception as e:
                logger.warning(f"Skipping invalid line: {e}")
    if batch:
        await db.quotes.insert_many(batch)
        inserted += len(batch)

    return {"inserted": inserted, "file": str(path)}


# Health endpoint
@api_router.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# -------- Telegram Bot Webhook (optional if token provided) --------

@api_router.post("/telegram/webhook/set", response_model=WebhookStatusResponse)
async def set_webhook(req: WebhookSetRequest):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_API_URL:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN is not configured on the server")

    secret_token = req.secret_token or uuid.uuid4().hex
    webhook_url = f"{req.public_url}/api/telegram/webhook/{secret_token}"

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(f"{TELEGRAM_API_URL}/setWebhook", json={
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"],
        })
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Failed to set webhook: {resp.text}")
        data = resp.json()
        if not data.get('ok'):
            raise HTTPException(status_code=400, detail=data.get('description', 'Telegram error'))

    webhook_config.update({
        "url": webhook_url,
        "secret_token": secret_token,
        "is_active": True,
        "last_update": datetime.utcnow(),
    })

    return WebhookStatusResponse(
        is_active=True,
        webhook_url=webhook_url,
        last_update=datetime.utcnow(),
        message="Webhook set successfully",
    )


@api_router.delete("/telegram/webhook", response_model=WebhookStatusResponse)
async def delete_webhook():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_API_URL:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN is not configured on the server")

    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(f"{TELEGRAM_API_URL}/deleteWebhook")
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Failed to delete webhook: {resp.text}")
        data = resp.json()
        if not data.get('ok'):
            raise HTTPException(status_code=400, detail=data.get('description', 'Telegram error'))

    webhook_config.update({
        "url": None,
        "secret_token": None,
        "is_active": False,
        "last_update": datetime.utcnow(),
    })

    return WebhookStatusResponse(
        is_active=False,
        webhook_url=None,
        last_update=datetime.utcnow(),
        message="Webhook deleted successfully",
    )


@api_router.get("/telegram/webhook/status", response_model=WebhookStatusResponse)
async def webhook_status():
    return WebhookStatusResponse(
        is_active=bool(webhook_config.get('is_active')),
        webhook_url=webhook_config.get('url'),
        last_update=webhook_config.get('last_update'),
        message="Current webhook status",
    )


@api_router.get("/telegram/bot/info")
async def bot_info():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_API_URL:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN is not configured on the server")
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(f"{TELEGRAM_API_URL}/getMe")
        return resp.json()


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[Dict[str, Any]] = None
    callback_query: Optional[Dict[str, Any]] = None


async def send_telegram_message(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_API_URL:
        logger.error("TELEGRAM_BOT_TOKEN missing. Cannot send messages.")
        return
    async with httpx.AsyncClient(timeout=20.0) as http:
        resp = await http.post(f"{TELEGRAM_API_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        })
        if resp.status_code != 200:
            logger.error(f"Failed to send message: {resp.text}")


async def get_random_quote_by_category(category: Optional[str] = None) -> Optional[Quote]:
    query = {"category": category} if category else {}
    count = await db.quotes.count_documents(query)
    if count == 0:
        return None
    # Random skip (not perfect for huge collections, but ok for demo; could use $sample)
    pipeline = []
    if category:
        pipeline.append({"$match": {"category": category}})
    pipeline.append({"$sample": {"size": 1}})
    docs = await db.quotes.aggregate(pipeline).to_list(length=1)
    if not docs:
        return None
    return Quote(**sanitize_mongo_doc(docs[0]))


@api_router.post("/telegram/webhook/{secret_token}")
async def telegram_webhook(secret_token: str, update: TelegramUpdate, background_tasks: BackgroundTasks):
    if webhook_config.get('secret_token') and secret_token != webhook_config.get('secret_token'):
        raise HTTPException(status_code=403, detail="Invalid secret token")

    webhook_config['last_update'] = datetime.utcnow()

    async def process_update():
        try:
            if update.message:
                chat_id = update.message.get('chat', {}).get('id')
                text = update.message.get('text', '') or ''
                if not chat_id:
                    return

                # Commands
                if text.startswith('/start'):
                    await send_telegram_message(chat_id, (
                        "Привет! Я бот с цитатами.\n"
                        "Команды:\n"
                        "/categories — список категорий\n"
                        "/quote &lt;категория&gt; — случайная цитата из категории (напр.: /quote Любовь)\n"
                        "/quote — случайная цитата из всех"
                    ))
                    return
                if text.startswith('/categories'):
                    cats = await db.quotes.distinct('category')
                    cats_text = "\n".join(f"• {c}" for c in sorted(cats)) if cats else "Категории не найдены. Импортируйте цитаты."
                    await send_telegram_message(chat_id, f"Категории:\n{cats_text}")
                    return
                if text.startswith('/quote'):
                    parts = text.split(maxsplit=1)
                    cat = parts[1].strip() if len(parts) &gt; 1 else None
                    quote = await get_random_quote_by_category(cat)
                    if not quote:
                        await send_telegram_message(chat_id, "Цитаты не найдены. Пожалуйста, импортируйте их на сервере.")
                        return
                    msg = f"\u275D {quote.text}\n— {quote.author or 'Неизвестный'}\nКатегория: {quote.category}"
                    await send_telegram_message(chat_id, msg)
                    return

                # Default: help hint
                await send_telegram_message(chat_id, "Напишите /quote или /categories")
        except Exception as e:
            logger.exception(f"Error processing telegram update: {e}")

    background_tasks.add_task(process_update)
    return {"status": "ok"}


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    # Useful indexes
    await db.quotes.create_index("category")
    await db.quotes.create_index("created_at")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()