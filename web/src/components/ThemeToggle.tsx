import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'

const STORAGE_KEY = 'kb-theme'

function currentTheme(): 'light' | 'dark' {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<'light' | 'dark'>(currentTheme)

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') root.classList.add('dark')
    else root.classList.remove('dark')
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* 隐私模式下 localStorage 不可写，主题仅在本次会话生效 */
    }
  }, [theme])

  return (
    <button
      type="button"
      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      className="inline-flex size-8 items-center justify-center rounded-full text-ink-2 transition-[background-color,color,transform] duration-150 ease-out hover:bg-sunken hover:text-ink active:scale-[0.94]"
      title={theme === 'dark' ? '切换到浅色' : '切换到深色'}
      aria-label={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
    >
      {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  )
}
