import {Button} from '@astryxdesign/core/Button';
import {Card} from '@astryxdesign/core/Card';
import {Icon} from '@astryxdesign/core/Icon';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {describeText, toParagraphs} from '../lib/text';

/** El <h1> del resultado. App mueve el foco aquí al cambiar de vista. */
export const RESULT_HEADING_ID = 'resultado-titulo';

type HeaderProps = {
  text: string;
  onCopy: () => Promise<void>;
  onDownload: () => void;
};

/**
 * Cabecera fija del resultado. Vive en el slot `header` del Layout, no dentro
 * del contenido: con una novela corregida debajo, unas acciones que scrollean
 * fuera de pantalla son acciones que no existen.
 */
export function ResultHeader({text, onCopy, onDownload}: HeaderProps) {
  return (
    <HStack
      justify="between"
      vAlign="center"
      gap={4}
      wrap="wrap"
      paddingInline={8}
      paddingBlock={4}>
      <VStack gap={1}>
        <Heading level={1} id={RESULT_HEADING_ID}>
          El texto, corregido
        </Heading>
        <Text type="supporting">{describeText(text)}</Text>
      </VStack>
      <HStack gap={2}>
        <Button label="Copiar" icon={<Icon icon="copy" />} clickAction={onCopy} />
        <Button
          label="Descargar"
          variant="primary"
          icon={<Icon icon="arrowDown" />}
          onClick={onDownload}
        />
      </HStack>
    </HStack>
  );
}

/** Pie fijo del resultado: la salida siempre a un clic, no a 2.600 párrafos. */
export function ResultFooter({onBack}: {onBack: () => void}) {
  return (
    <HStack paddingInline={8} paddingBlock={4}>
      <Button
        label="Volver al manuscrito"
        variant="ghost"
        icon={<Icon icon="chevronLeft" />}
        onClick={onBack}
      />
    </HStack>
  );
}

export default function ResultView({text}: {text: string}) {
  const paragraphs = toParagraphs(text);

  return (
    /*
      La hoja. Card aquí es legítimo: es un objeto autocontenido, no una
      lista envuelta. Tipografía de lectura vía tokens del tema — serifa de
      titulares (Fraunces) al cuerpo, interlineado holgado.
    */
    <Card padding={6}>
      <VStack
        as="article"
        gap={4}
        style={{
          fontFamily: 'var(--font-family-heading)',
          fontSize: 'var(--font-size-lg)',
          lineHeight: 'var(--text-supporting-leading)',
          color: 'var(--color-text-primary)',
        }}>
        {paragraphs.map((paragraph, index) => (
          <Text key={index} as="p" type="inherit" textWrap="pretty">
            {paragraph}
          </Text>
        ))}
      </VStack>
    </Card>
  );
}
