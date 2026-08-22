import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {Theme} from '@astryxdesign/core';
import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import {chocolateTheme} from './theme/chocolate.js';
import {chocolateIconRegistry} from './theme/icons';
import './theme/chocolate.css';
import './index.css';
import App from './App.tsx';

/*
 * Tokens desde el CSS ya compilado (`astryx theme build`), no inyectados en
 * runtime. El registro de iconos es JSX y no sobrevive al compilador del
 * tema, así que se engancha aquí sobre el objeto construido.
 */
const theme = {...chocolateTheme, icons: chocolateIconRegistry};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Theme theme={theme} mode="light">
      <App />
    </Theme>
  </StrictMode>,
);
