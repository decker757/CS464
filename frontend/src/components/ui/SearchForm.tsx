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

/** A labelled search box and Search button, trimming on submit, for the admin
 * user search and the market filters. When `query` changes, the box shows it,
 * so after a submit the box holds the trimmed search. Adjusted during render,
 * React's documented alternative to an effect for resetting state when a prop
 * changes, so clearing an applied search also empties text typed over it and
 * never submitted. */
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
