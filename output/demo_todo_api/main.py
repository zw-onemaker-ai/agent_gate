"""Todo List REST API — FastAPI"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

app = FastAPI(title="Todo API", version="0.1.0")

todos: dict[int, dict] = {}
next_id = 1

class TodoCreate(BaseModel):
    title: str

class TodoResponse(BaseModel):
    id: int
    title: str
    completed: bool = False

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/todos", response_model=TodoResponse)
def create_todo(item: TodoCreate):
    global next_id
    todo = {"id": next_id, "title": item.title, "completed": False}
    todos[next_id] = todo
    next_id += 1
    return todo

@app.get("/todos", response_model=list[TodoResponse])
def list_todos():
    return [TodoResponse(**t) for t in todos.values()]

@app.patch("/todos/{todo_id}/complete", response_model=TodoResponse)
def complete_todo(todo_id: int):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    todos[todo_id]["completed"] = True
    return TodoResponse(**todos[todo_id])

@app.delete("/todos/{todo_id}")
def delete_todo(todo_id: int):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    del todos[todo_id]
    return {"status": "deleted"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
