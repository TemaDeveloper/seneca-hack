import { StrictMode, Component } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, info) {
    console.error('React ErrorBoundary caught:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: '48px', fontFamily: 'Inter, sans-serif',
          background: '#fff', minHeight: '100vh'
        }}>
          <h1 style={{ fontWeight: 800, textTransform: 'uppercase', borderBottom: '4px solid #000', paddingBottom: '16px' }}>
            Something went wrong
          </h1>
          <pre style={{
            background: '#f5f5f5', padding: '24px', border: '2px solid #000',
            overflow: 'auto', fontSize: '14px', lineHeight: 1.6
          }}>
            {this.state.error?.toString()}
          </pre>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: '24px', padding: '12px 24px', fontWeight: 800,
              fontSize: '1.1rem', textTransform: 'uppercase', cursor: 'pointer',
              border: '4px solid #000', background: '#fff',
              boxShadow: '4px 4px 0px #000'
            }}
          >
            Reload Page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
