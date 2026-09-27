import { controlClass } from './controlClass'

export interface TextInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
}

export default function TextInput({ invalid = false, className = '', ...props }: TextInputProps) {
  return <input {...props} aria-invalid={invalid || undefined} className={`${controlClass(invalid)} h-[50px] ${className}`} />
}
