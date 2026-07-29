# from fastapi import FastAPI

# app = FastAPI()

# @app.get("/")
# def root():
#     return {"message": "Hello FastAPI!"}

# @app.get("/hello/{name}")
# def hello(name: str):
#     return {"message": f"Hello, {name}!"}


# from fastapi import FastAPI

# app = FastAPI()

# @app.get("/items")           # 查询
# async def get_items():
#     return [{"id": 1, "name": "Item1"}]

# @app.post("/items")          # 创建
# async def create_item():
#     return {"message": "Item created"}

# @app.put("/items/{item_id}") # 完整更新
# async def update_item(item_id: int):
#     return {"item_id": item_id, "updated": True}

# @app.patch("/items/{item_id}") # 部分更新
# async def patch_item(item_id: int):
#     return {"item_id": item_id, "patched": True}

# @app.delete("/items/{item_id}") # 删除
# async def delete_item(item_id: int):
#     return {"item_id": item_id, "deleted": True}









# @app.get("/users/{user_id}")
# async def get_user(user_id: int):  # 自动类型转换
#     return {"user_id": user_id, "name": f"User {user_id}"}

# # 多个路径参数
# @app.get("/users/{user_id}/posts/{post_id}")
# async def get_post(user_id: int, post_id: int):
#     return {"user_id": user_id, "post_id": post_id}

# # 路径参数枚举
# from enum import Enum

# class Color(str, Enum):
#     RED = "red"
#     BLUE = "blue"
#     GREEN = "green"

# @app.get("/colors/{color}")
# async def get_color(color: Color):
#     return {"color": color, "hex": "#FF0000" if color == Color.RED else "#0000FF"}
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI, HTTPException, status, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


app = FastAPI(title="博客 API", version="1.0.0")


app.mount("/static", StaticFiles(directory="static", html=True), name="static")
# ===== 数据模型 =====
class PostCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default=[])
    category: str = Field(default="未分类")

class PostResponse(BaseModel):
    id: int
    title: str
    content: str
    tags: List[str]
    category: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    view_count: int = 0

class PostUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1)
    tags: Optional[List[str]] = None
    category: Optional[str] = None

# ===== 模拟数据库 =====
posts_db = {}
counter = 1

# ===== 依赖注入 =====
async def get_post_or_404(post_id: int) -> dict:
    post = posts_db.get(post_id)
    if not post:
        raise HTTPException(status_code=404, detail=f"文章 ID {post_id} 不存在")
    return post

# ===== API 路由 =====
@app.post("/posts/", response_model=PostResponse, status_code=201)
async def create_post(post: PostCreate):
    global counter
    new_post = {
        "id": counter,
        "title": post.title,
        "content": post.content,
        "tags": post.tags,
        "category": post.category,
        "created_at": datetime.now(),
        "updated_at": None,
        "view_count": 0
    }
    posts_db[counter] = new_post
    counter += 1
    return new_post

@app.get("/posts/", response_model=List[PostResponse])
async def list_posts(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    category: Optional[str] = None,
    tag: Optional[str] = None
):
    posts = list(posts_db.values())
    
    if category:
        posts = [p for p in posts if p["category"] == category]
    if tag:
        posts = [p for p in posts if tag in p["tags"]]
    
    return posts[skip:skip + limit]

@app.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(post: dict = Depends(get_post_or_404)):
    # 增加浏览次数
    post["view_count"] += 1
    return post

@app.put("/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_update: PostUpdate,
    post: dict = Depends(get_post_or_404)
):
    update_data = post_update.model_dump(exclude_unset=True)
    post.update(update_data)
    post["updated_at"] = datetime.now()
    return post

@app.delete("/posts/{post_id}", status_code=204)
async def delete_post(
    post: dict = Depends(get_post_or_404)
):
    del posts_db[post["id"]]
    return None

@app.get("/posts/stats/categories")
async def get_category_stats():
    """统计各分类的文章数量"""
    stats = {}
    for post in posts_db.values():
        category = post["category"]
        stats[category] = stats.get(category, 0) + 1
    return stats