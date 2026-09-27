import { type Size, type Variant, buttonClass } from './buttonClass'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant: Variant
  size: Size
}

export default function Button({ variant, size, className = '', type = 'button', ...props }: ButtonProps) {
  return <button type={type} {...props} className={`${buttonClass(variant, size)} ${className}`} />
}
