from src.storage import get_db

db = get_db()
# 最近 30 天，最多 50 条（可指定 code）
records = db.get_analysis_history(code=None, days=30, limit=50)
for r in records:
    print(r.created_at, r.code, r.name, r.operation_advice, r.sentiment_score)