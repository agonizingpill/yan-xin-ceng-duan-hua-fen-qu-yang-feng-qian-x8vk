import os,hmac,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError
from redis import Redis
from .database import Session,Base,engine
from .models import User
from .schemas import Login
from .security import password_hash,token_for
from .middlewares import RequestTrace,Recovery
from scripts.seed_demo import seed
logging.basicConfig(level=logging.INFO)
redis=Redis.from_url(os.environ['REDIS_URL'],decode_responses=True)
# create_all 不会给既有库补列；演示环境启动时幂等补齐新增字段。
MIGRATIONS=[('boreholes','work_area',"VARCHAR(120) DEFAULT ''"),('interpretations','kind',"VARCHAR(20) DEFAULT 'gmm'"),('interpretations','source_digest',"VARCHAR(64) DEFAULT ''"),('interpretations','invalid_reason',"VARCHAR(600) DEFAULT ''"),('interpretations','source_invalidated','BOOLEAN DEFAULT FALSE')]
def migrate():
    with engine.begin()as c:
        for table,column,ddl in MIGRATIONS:
            exists=c.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"),{'t':table,'c':column}).scalar()
            if not exists:c.execute(text('ALTER TABLE '+table+' ADD COLUMN '+column+' '+ddl))
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    try:migrate()
    except Exception:logging.exception('migration failed')
    if os.getenv('SEED_DEMO','true')=='true':
        try:seed()
        except Exception:logging.exception('seed failed')
    yield
app=FastAPI(title='砾衡 · 岩芯层段与封签簿',lifespan=lifespan);app.add_middleware(RequestTrace);app.add_middleware(Recovery)
from .routes import router
app.include_router(router)
@app.exception_handler(IntegrityError)
async def duplicate(request,e):return JSONResponse({'detail':'编号/深度重复或关联不合法，记录未提交'},status_code=409)
@app.get('/health')
def health():
    with Session()as s:s.execute(text('SELECT 1'))
    return {'status':'ok','redis':redis.ping()}
@app.post('/auth/login')
def login(p:Login):
    with Session()as s:
        u=s.scalar(select(User).where(User.email==p.email))
        if not u or not hmac.compare_digest(u.password,password_hash(p.password)):raise HTTPException(401,'账号或口令不正确')
        return {'token':token_for(u),'role':u.role,'email':u.email}
