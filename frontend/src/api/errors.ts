import axios from 'axios'

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

export const GENERIC_ERROR = 'Something went wrong. Please try again.'

/** The backend's `error.code`, or undefined for anything that is not an API error. */
export function errorCode(err: unknown): string | undefined {
  return axios.isAxiosError<ApiError>(err) ? err.response?.data?.error?.code : undefined
}

/** The backend's `error.details`, or an empty list. */
export function errorDetails(err: unknown): { field: string; message: string }[] {
  if (!axios.isAxiosError<ApiError>(err)) return []
  return err.response?.data?.error?.details ?? []
}

interface FormErrorOptions {
  /** The form's fields; a 422 about one of these is shown under that field. */
  fields: readonly string[]
  /** A friendlier message for particular error codes. */
  codeMessages?: Record<string, string>
  /** Codes whose `details` list field-by-field messages, like `duplicate_user`. */
  fieldErrorCodes?: readonly string[]
}

/** What a failed form submission should show. Only one of the two is ever set. */
export interface FormErrorView {
  fieldErrors: Record<string, string>
  formError: string
}

/** Turn a failed request into either per-field messages or one message for the whole form. */
export function describeFormError(
  err: unknown,
  { fields, codeMessages = {}, fieldErrorCodes = [] }: FormErrorOptions,
): FormErrorView {
  if (!axios.isAxiosError(err)) return { fieldErrors: {}, formError: GENERIC_ERROR }
  const data = err.response?.data as (ApiError & FastApiError) | undefined

  // FastAPI 422: pydantic rejected a field
  const first = data?.detail?.[0]
  if (first) {
    const field = first.loc[first.loc.length - 1]
    if (fields.includes(field)) return { fieldErrors: { [field]: first.msg }, formError: '' }
    return { fieldErrors: {}, formError: first.msg || GENERIC_ERROR }
  }

  const code = data?.error?.code
  if (code && codeMessages[code]) return { fieldErrors: {}, formError: codeMessages[code] }
  if (code && fieldErrorCodes.includes(code)) {
    const fieldErrors = Object.fromEntries(errorDetails(err).map((d) => [d.field, d.message]))
    return { fieldErrors, formError: '' }
  }
  return { fieldErrors: {}, formError: data?.error?.message || GENERIC_ERROR }
}
