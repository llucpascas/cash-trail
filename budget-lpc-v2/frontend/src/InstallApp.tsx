import { useEffect, useState } from 'react';
import { Download, Smartphone, Check } from 'lucide-react';

interface InstallPrompt extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

export default function InstallApp() {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null);
  const [installed, setInstalled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
  useEffect(() => {
    const mode = window.matchMedia('(display-mode: standalone)');
    const refresh = () => setInstalled(mode.matches || Boolean((navigator as Navigator & { standalone?: boolean }).standalone));
    const available = (event: Event) => { event.preventDefault(); setPrompt(event as InstallPrompt); };
    const completed = () => { setInstalled(true); setPrompt(null); };
    refresh(); mode.addEventListener('change', refresh);
    window.addEventListener('beforeinstallprompt', available);
    window.addEventListener('appinstalled', completed);
    return () => { mode.removeEventListener('change', refresh); window.removeEventListener('beforeinstallprompt', available); window.removeEventListener('appinstalled', completed); };
  }, []);
  async function install() {
    if (!prompt) return;
    setBusy(true);
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      setMessage(choice.outcome === 'accepted' ? 'Installation requested. Look for Budget LPC on your home screen.' : 'You can install later from your browser menu.');
      setPrompt(null);
    } catch { setMessage('Use your browser menu to add Budget LPC to your home screen.'); }
    finally { setBusy(false); }
  }
  return <section className="panel install-app">
    <div className="install-identity"><img src="/icons/icon-192.png" alt="Budget LPC app icon"/><div><span className="eyebrow">TAKE YOUR BUDGET WITH YOU</span><h2>Budget LPC on your phone</h2><p>Your LPC logo. Your home screen. A dedicated app window.</p></div></div>
    {installed ? <p className="notice"><Check size={18}/>You’re using the installed Budget LPC app.</p> : <>
      {local && <p className="notice"><Smartphone size={19}/><span>This address works only on this computer. A secure phone-accessible address needs to be connected before installing on your phone.</span></p>}
      {!local && !window.isSecureContext && <p className="notice">Open Budget LPC over a secure HTTPS connection to install it.</p>}
      {prompt && <button className="primary" disabled={busy} onClick={() => void install()}><Download size={17}/>{busy ? 'Opening installation…' : 'Install Budget LPC on this device'}</button>}
      <div className="install-instructions"><div><h3>iPhone</h3><p>Open your Budget LPC address in Safari. Tap <strong>Share → Add to Home Screen</strong>, keep the name “Budget LPC”, and choose <strong>Add</strong>. Enable “Open as Web App” if shown.</p></div><div><h3>Android</h3><p>Open your Budget LPC address in Chrome. Choose <strong>Install app</strong> from the browser menu, or use the install button when available.</p></div></div>
    </>}
    {message && <p role="status" className="muted">{message}</p>}
    <p className="muted">Your phone connects to the same database as the website. The server must be reachable to view or change records. No offline financial changes are queued.</p>
  </section>;
}

