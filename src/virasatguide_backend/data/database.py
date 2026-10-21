from motor.motor_asyncio import AsyncIOMotorClient

# In production, load this URL from environment variables
client = AsyncIOMotorClient("mongodb://localhost:27017")
db = client.fastapi_jwt_db
users_collection = db.users