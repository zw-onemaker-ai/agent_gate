# Requirements Document
## Product Idea
Simple Todo List REST API

## User Stories
- As a user, I want to create a todo item so that I can track tasks
- As a user, I want to list all todos so that I can see what's pending
- As a user, I want to mark a todo as complete so that I can track progress
- As a user, I want to delete a todo so that I can remove finished items

## Acceptance Criteria
1. POST /todos creates a new todo with title and returns it with an id
2. GET /todos returns a list of all todos
3. PATCH /todos/{id}/complete marks a todo as done
4. DELETE /todos/{id} removes a todo
5. GET /health returns {"status": "ok"}

## MVP Scope
- In-memory storage (no database)
- FastAPI with 5 endpoints
- JSON request/response

## Scenario Type: personal