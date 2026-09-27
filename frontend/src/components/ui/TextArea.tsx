import { controlClass } from './controlClass'

interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean
}

export default function TextArea({ invalid = false, className = '', ...props }: TextAreaProps) {
  return <textarea {...props} className={`${controlClass(invalid)} min-h-[100px] resize-y py-3 ${className}`} />
}
