import {useEffect, useRef, useState} from 'react';
import {AppShell} from '@astryxdesign/core/AppShell';
import {
  Layout,
  LayoutContent,
  LayoutFooter,
  LayoutHeader,
} from '@astryxdesign/core/Layout';
import {NavIcon} from '@astryxdesign/core/NavIcon';
import {TopNav, TopNavHeading} from '@astryxdesign/core/TopNav';
import {useToast} from '@astryxdesign/core/Toast';
import {VisuallyHidden} from '@astryxdesign/core/VisuallyHidden';
import {Feather} from 'lucide-react';
import {proofread} from './lib/proofread';
import {correctedFileName, describeText, downloadText} from './lib/text';
import type {Source, Stage} from './types';
import ComposeView, {COMPOSE_HEADING_ID} from './views/ComposeView';
import ResultView, {
  RESULT_HEADING_ID,
  ResultFooter,
  ResultHeader,
} from './views/ResultView';

export default function App() {
  const showToast = useToast();

  const [stage, setStage] = useState<Stage>('compose');
  const [source, setSource] = useState<Source>('write');
  const [text, setText] = useState('');
  const [corrected, setCorrected] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [isWorking, setIsWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');

  // En "subir archivo" sólo cuenta lo que hay en la zona de subida: sin
  // archivo no hay manuscrito, aunque quede texto tecleado en la otra
  // pestaña. Sin esto, la app corrige un texto que no se ve por ningún lado.
  const activeText = source === 'upload' && !file ? '' : text;

  // Cambiar de vista desmonta el foco y lo deja en <body>: quien navega con
  // teclado vuelve al principio del documento y quien usa lector de pantalla
  // no se entera de nada. Movemos el foco al título de la vista nueva.
  // Comparamos el valor anterior en vez de "¿es el primer render?": en
  // StrictMode el efecto se invoca dos veces al montar, y un flag de primera
  // vez deja pasar la segunda y roba el foco nada más cargar la página.
  const previousStage = useRef(stage);
  useEffect(() => {
    if (previousStage.current === stage) return;
    previousStage.current = stage;
    const heading = document.getElementById(
      stage === 'result' ? RESULT_HEADING_ID : COMPOSE_HEADING_ID,
    );
    if (!heading) return;
    heading.tabIndex = -1;
    heading.focus();
  }, [stage]);

  const handleSourceChange = (next: Source) => {
    setSource(next);
    setError(null);
  };

  const handleFileChange = async (next: File | null) => {
    setFile(next);
    setError(null);
    if (!next) {
      setText('');
      return;
    }
    try {
      setText(await next.text());
    } catch {
      setText('');
      setError('No se ha podido leer el archivo.');
    }
  };

  const handleSubmit = async () => {
    setIsWorking(true);
    setError(null);
    try {
      const result = await proofread(activeText);
      setCorrected(result);
      setStage('result');
      setAnnouncement(`Texto corregido. ${describeText(result)}.`);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'El corrector no ha podido terminar.',
      );
    } finally {
      setIsWorking(false);
    }
  };

  const handleClear = () => {
    setText('');
    setFile(null);
    setError(null);
  };

  const handleBack = () => {
    setStage('compose');
    setAnnouncement('De vuelta al manuscrito.');
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(corrected);
      showToast({body: 'Texto copiado al portapapeles', uniqueID: 'copiar'});
    } catch {
      showToast({
        body: 'El navegador ha bloqueado el portapapeles',
        type: 'error',
        uniqueID: 'copiar',
      });
    }
  };

  const handleDownload = () => {
    downloadText(corrected, correctedFileName(file?.name ?? null));
  };

  const isResult = stage === 'result';

  return (
    // Contrato responsive:
    //   > 760px   columna de lectura centrada a 760, márgenes al aire
    //   <= 760px  la columna ocupa el ancho disponible; las acciones envuelven
    //   <= 768px  AppShell pliega la navegación en su cajón móvil
    <AppShell
      height="fill"
      contentPadding={0}
      topNav={
        <TopNav
          label="Navegación principal"
          heading={
            <TopNavHeading
              heading="Amanuense"
              subheading="Corrector literario"
              logo={<NavIcon icon={<Feather size={16} />} />}
            />
          }
        />
      }>
      <Layout
        height="fill"
        contentWidth={760}
        header={
          isResult ? (
            <LayoutHeader hasDivider>
              <ResultHeader
                text={corrected}
                onCopy={handleCopy}
                onDownload={handleDownload}
              />
            </LayoutHeader>
          ) : undefined
        }
        footer={
          isResult ? (
            <LayoutFooter hasDivider>
              <ResultFooter onBack={handleBack} />
            </LayoutFooter>
          ) : undefined
        }
        content={
          <LayoutContent padding={8} label="Corrector de textos">
            <VisuallyHidden as="div" aria-live="polite">
              {announcement}
            </VisuallyHidden>
            {isResult ? (
              <ResultView text={corrected} />
            ) : (
              <ComposeView
                text={text}
                activeText={activeText}
                source={source}
                file={file}
                isWorking={isWorking}
                error={error}
                onTextChange={setText}
                onSourceChange={handleSourceChange}
                onFileChange={handleFileChange}
                onSubmit={handleSubmit}
                onClear={handleClear}
              />
            )}
          </LayoutContent>
        }
      />
    </AppShell>
  );
}
