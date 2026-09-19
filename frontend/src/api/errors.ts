export interface ApiError {
  error: {
    code: string
    message: string
    details?: { field: string; message: string }[]
  }
}
