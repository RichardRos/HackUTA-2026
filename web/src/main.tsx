import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/react'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY
if (!publishableKey) throw new Error('Set VITE_CLERK_PUBLISHABLE_KEY in web/.env.local')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ClerkProvider
      publishableKey={publishableKey}
      afterSignOutUrl="/"
      appearance={{
        variables: {
          colorPrimary: '#7bf1b1',
          colorBackground: '#10191d',
          colorForeground: '#e6ecee',
          colorMutedForeground: '#8a979b',
          colorPrimaryForeground: '#06090c',
          colorInput: '#06090c',
          colorInputForeground: '#e6ecee',
          colorBorder: '#2d3d43',
          fontFamily: 'Inter, system-ui, sans-serif',
          borderRadius: '2px',
        },
      }}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ClerkProvider>
  </StrictMode>,
)
