export interface ApiError {
  error: {
    code: string
    message: string
    details?: { field: string; message: string }[]
  }
}

// FastAPI 422 shape: returned when pydantic rejects a field (e.g. malformed email)
export interface FastApiError {
  detail?: { loc: string[]; msg: string; type: string }[]
}
