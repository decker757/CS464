import axios from 'axios'
import { formatCreditsPrecise, isPlainCredits, isZeroCredits, subtractCredits } from '../utils/formatCredits'

export interface ApiError {
  error: {
    code: string
    message: string
    // Most codes list field-by-field messages. A handful of ledger trade
    // codes (quote_stale, insufficient_funds, insufficient_shares_held) send
    // a flat object instead, and invalid_request's details are pydantic's own
    // {loc, msg, type} — read those with tradeErrorDetails, not this.
    details?: { field: string; message: string }[]
  }
}

// One entry of a FastAPI 422: pydantic rejected a field (e.g. malformed email).
// `loc` is the path to it, like ['body', 'email']; a list index is a number.
interface ValidationEntry {
  loc: (string | number)[]
  msg: string
  type: string
}

// FastAPI's own errors. A 422 sends `detail` as a list of entries; others,
// like its 404 for an unknown route, send it as a plain string.
export interface FastApiError {
  detail?: ValidationEntry[] | string
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

/**
 * `error.details` for the ledger trade codes that send a flat object rather
 * than a field-message list — quote_stale's `{quoted, current}`,
 * insufficient_funds's `{balance, required, reserved, available}`,
 * insufficient_shares_held's `{held, requested, reserved}`
 * (ledger-service.md). Read the keys that code names; nothing validates the
 * shape beyond "an object, or nothing."
 */
export function tradeErrorDetails(err: unknown): Record<string, unknown> {
  if (!axios.isAxiosError(err)) return {}
  const details = (err.response?.data as { error?: { details?: unknown } } | undefined)?.error?.details
  return details && typeof details === 'object' && !Array.isArray(details) ? details as Record<string, unknown> : {}
}

/**
 * Whether a failed request got a definite answer from the server — a 2xx
 * would not be here, so this means a 4xx — versus no response at all
 * (network error, timeout) or a 5xx. A trade's idempotency key must only be
 * replaced once the answer is definite: a lost reply or a 5xx means the
 * server may have committed the trade anyway, and the only safe retry is
 * one that reuses the key so the server's own replay recognises it
 * (ledger-service.md's idempotency section).
 */
export function isDefiniteRejection(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false
  const status = err.response?.status
  return status !== undefined && status >= 400 && status < 500
}

// Trade/preview codes with a fixed message (ledger-service.md). Codes with
// structured details (quote_stale, insufficient_funds,
// insufficient_shares_held) are built from those details instead, below.
const TRADE_FIXED_MESSAGES: Record<string, string> = {
  market_closed: 'This market has closed. Trading is no longer possible.',
  idempotency_key_reused: 'This trade already went through under a different request. Reload the page to see your position.',
  unknown_outcome: 'That outcome is no longer part of this market. Reload the page.',
  quantity_too_large: 'That quantity is too large for this market.',
  cost_below_tick: 'That quantity costs less than the smallest amount this market can charge. Try a larger quantity.',
  proceeds_below_tick: 'That quantity would pay out less than the smallest amount this market can pay. Try a larger quantity.',
  insufficient_shares_outstanding: 'Nobody holds enough shares of this outcome yet for a sell this size.',
  market_not_found: 'This market is not available.',
  invalid_request: "That quantity isn't valid.",
}

/**
 * Whether a reservation figure is worth a sentence of its own: a decimal
 * string above zero. Missing it — the panel's own pre-send check has no such
 * figure — or a value that is not a plain decimal, or exactly zero, all mean
 * the same thing for wording: this trader has no open orders to be told
 * about, so the message must not mention any (#411).
 */
function isReserving(reserved: string | undefined): reserved is string {
  return reserved !== undefined && isPlainCredits(reserved) && !isZeroCredits(reserved)
}

/**
 * The two refusals a screen can see coming, worded once. The trading panel
 * checks the balance and the holding it already has before it sends anything
 * (#357), and the ledger answers `insufficient_funds` or
 * `insufficient_shares_held` when its own figures disagree — the trader
 * reads the same sentence whichever side caught it.
 *
 * The ledger refuses a buy on `available`, the balance less what the
 * trader's own open buy orders reserve ([T-6] #26), so that is the figure the
 * message names, with a second sentence for where the rest of the balance
 * went. `available` is the ledger's own answer rather than a subtraction
 * here, because it is the number the refusal was made on. #411.
 */
export function describeInsufficientFunds(
  balance: string,
  required: string,
  openOrders?: { reserved: string; available: string },
): string {
  const withoutOrders = `You have ${formatCreditsPrecise(balance)} credits, but this trade needs ${formatCreditsPrecise(required)}.`
  if (!openOrders || !isReserving(openOrders.reserved) || !isPlainCredits(openOrders.available)) return withoutOrders

  const refusal = `You have ${formatCreditsPrecise(openOrders.available)} credits available, but this trade needs ${formatCreditsPrecise(required)}.`
  const whereTheRestWent = `Your balance is ${formatCreditsPrecise(balance)}, and ${formatCreditsPrecise(openOrders.reserved)} of it is held by your open buy orders.`
  return `${refusal} ${whereTheRestWent}`
}

/**
 * The same shape for a sell, which the ledger refuses on the position less
 * what the trader's own open sell orders reserve. `details` names `held` and
 * `reserved` and no third figure, so what is left to sell is the one
 * subtraction — exact, over the decimal strings, never a parsed float.
 * "Held" stays the whole position, the way the contract uses the word, so
 * the shares the orders take are *reserved* rather than held. #411.
 */
export function describeInsufficientShares(held: string, requested: string, reserved?: string): string {
  const withoutOrders = `You hold ${formatCreditsPrecise(held)} shares, but this sell asks for ${formatCreditsPrecise(requested)}.`
  if (!isReserving(reserved)) return withoutOrders

  const available = subtractCredits(held, reserved)
  if (available === null) return withoutOrders

  const refusal = `You have ${formatCreditsPrecise(available)} shares available to sell, but this sell asks for ${formatCreditsPrecise(requested)}.`
  const whereTheRestWent = `You hold ${formatCreditsPrecise(held)} in all, and ${formatCreditsPrecise(reserved)} of them are reserved by your open sell orders.`
  return `${refusal} ${whereTheRestWent}`
}

/** One `error.details` figure, when the ledger sent it as a decimal string. */
function tradeErrorFigure(details: Record<string, unknown>, key: string): string | undefined {
  const value = details[key]
  return typeof value === 'string' ? value : undefined
}

/** What the trader should do about a failed preview or trade (ledger-service.md). */
export function describeTradeError(err: unknown): string {
  const code = errorCode(err)
  if (!code) return GENERIC_ERROR
  const details = tradeErrorDetails(err)
  if (code === 'quote_stale') return 'Prices moved while you were looking. Getting a fresh quote…'
  if (code === 'insufficient_funds') {
    const balance = tradeErrorFigure(details, 'balance')
    const required = tradeErrorFigure(details, 'required')
    const reserved = tradeErrorFigure(details, 'reserved')
    const available = tradeErrorFigure(details, 'available')
    if (balance !== undefined && required !== undefined) {
      const openOrders = reserved !== undefined && available !== undefined ? { reserved, available } : undefined
      return describeInsufficientFunds(balance, required, openOrders)
    }
  }
  if (code === 'insufficient_shares_held') {
    const held = tradeErrorFigure(details, 'held')
    const requested = tradeErrorFigure(details, 'requested')
    if (held !== undefined && requested !== undefined) {
      return describeInsufficientShares(held, requested, tradeErrorFigure(details, 'reserved'))
    }
  }
  return TRADE_FIXED_MESSAGES[code] ?? GENERIC_ERROR
}

interface FormErrorOptions {
  /** The form's fields; a 422 about one of these is shown under that field. */
  fields: readonly string[]
  /** A friendlier message for particular error codes. */
  codeMessages?: Record<string, string>
  /** Codes whose `details` list field-by-field messages, like `duplicate_user`. */
  fieldErrorCodes?: readonly string[]
}

/**
 * What a failed form submission should show. A 422 can set both: a message
 * under each field it names, and one above the form for anything else.
 */
export interface FormErrorView {
  fieldErrors: Record<string, string>
  formError: string
}

/**
 * Sort a 422's entries into the form's fields. Each field keeps its first
 * message; the first entry that names no field becomes the form message.
 */
function describeValidationErrors(entries: ValidationEntry[], fields: readonly string[]): FormErrorView {
  const fieldErrors: Record<string, string> = {}
  let formError = ''
  for (const entry of entries) {
    const field = String(entry.loc[entry.loc.length - 1])
    if (fields.includes(field)) {
      if (!(field in fieldErrors)) fieldErrors[field] = entry.msg
    } else if (!formError) {
      formError = entry.msg || GENERIC_ERROR
    }
  }
  if (Object.keys(fieldErrors).length === 0 && !formError) formError = GENERIC_ERROR
  return { fieldErrors, formError }
}

/** Turn a failed request into per-field messages, one message for the whole form, or both. */
export function describeFormError(
  err: unknown,
  { fields, codeMessages = {}, fieldErrorCodes = [] }: FormErrorOptions,
): FormErrorView {
  if (!axios.isAxiosError(err)) return { fieldErrors: {}, formError: GENERIC_ERROR }
  const data = err.response?.data as (ApiError & FastApiError) | undefined

  const detail = data?.detail
  if (Array.isArray(detail)) return describeValidationErrors(detail, fields)

  const code = data?.error?.code
  if (code && codeMessages[code]) return { fieldErrors: {}, formError: codeMessages[code] }

  // A code like duplicate_user normally names its fields in `details`. When
  // it names none, fall through to its message rather than show nothing.
  const details = errorDetails(err)
  if (code && fieldErrorCodes.includes(code) && details.length > 0) {
    const fieldErrors = Object.fromEntries(details.map((d) => [d.field, d.message]))
    return { fieldErrors, formError: '' }
  }
  return { fieldErrors: {}, formError: data?.error?.message || GENERIC_ERROR }
}
