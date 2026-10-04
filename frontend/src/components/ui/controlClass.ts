/** The shared look of a text input or text area, red-bordered when `invalid`. */
export function controlClass(invalid: boolean) {
  const border = invalid ? 'border-danger' : 'border-line'
  return `w-full rounded-control border ${border} bg-white px-3.5 text-[15px] text-smu-navy outline-none transition focus:border-smu-navy focus:ring-3 focus:ring-smu-navy/8`
}
