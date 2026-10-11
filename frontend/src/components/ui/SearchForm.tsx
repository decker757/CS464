import { useState, type FormEvent } from 'react'
import Button from './Button'
import TextInput from './TextInput'

interface SearchFormProps {
  id: string
  label: string
  placeholder: string
  /** The search that is applied to the list, not what is half typed in the box. */
  query: string
  onSearch: (query: string) => void
}

/** A labelled search box and Search button, trimming on submit — the admin
 * user search's box, which MarketFilters (#255) should adopt. The box's draft
 * text follows `query` when it is cleared from outside (#255's "Clear
 * search"/"Clear all" will need this), but not on every keystroke of a submit
 * it caused itself — `query` only changes a beat after submit, to the same
 * trimmed value already in the box. Adjusted during render, React's documented
 * alternative to an effect for resetting state when a prop changes. */
export default function SearchForm({ id, label, placeholder, query, onSearch }: SearchFormProps) {
  const [draft, setDraft] = useState(query)
  const [lastQuery, setLastQuery] = useState(query)
  if (query !== lastQuery) {
    setLastQuery(query)
    setDraft(query)
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    onSearch(draft.trim())
  }

  return (
    <form role="search" onSubmit={handleSubmit} className="flex gap-2">
      <label htmlFor={id} className="sr-only">{label}</label>
      <TextInput
        id={id}
        type="search"
        value={draft}
        onChange={event => setDraft(event.target.value)}
        placeholder={placeholder}
      />
      <Button type="submit" variant="outline" size="md">Search</Button>
    </form>
  )
}
