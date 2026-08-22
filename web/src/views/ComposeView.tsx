import {useDeferredValue, useMemo} from 'react';
import {Button} from '@astryxdesign/core/Button';
import {Divider} from '@astryxdesign/core/Divider';
import {FileInput} from '@astryxdesign/core/FileInput';
import {
  SegmentedControl,
  SegmentedControlItem,
} from '@astryxdesign/core/SegmentedControl';
import {HStack, VStack} from '@astryxdesign/core/Stack';
import {Heading, Text} from '@astryxdesign/core/Text';
import {TextArea} from '@astryxdesign/core/TextArea';
import {describeText, hasContent} from '../lib/text';
import type {Source} from '../types';

const ACCEPTED = '.txt,.md,.markdown,.text';
const MAX_FILE_BYTES = 2 * 1024 * 1024;

/** El <h1> del manuscrito. App mueve el foco aquí al volver del resultado. */
export const COMPOSE_HEADING_ID = 'manuscrito-titulo';

type Props = {
  /** Lo que se edita en el textarea. */
  text: string;
  /** Lo que cuenta de verdad según la fuente activa (ver App). */
  activeText: string;
  source: Source;
  file: File | null;
  isWorking: boolean;
  error: string | null;
  onTextChange: (value: string) => void;
  onSourceChange: (source: Source) => void;
  onFileChange: (file: File | null) => void;
  onSubmit: () => void;
  onClear: () => void;
};

export default function ComposeView({
  text,
  activeText,
  source,
  file,
  isWorking,
  error,
  onTextChange,
  onSourceChange,
  onFileChange,
  onSubmit,
  onClear,
}: Props) {
  const isEmpty = !hasContent(activeText);

  // El recuento cuesta O(texto) y una novela son 200.000 caracteres: recontar
  // en cada tecla bloquea el tecleo. useDeferredValue deja que el textarea
  // pinte a prioridad normal y el contador se ponga al día después.
  const deferredText = useDeferredValue(activeText);
  const summary = useMemo(() => describeText(deferredText), [deferredText]);

  return (
    <VStack gap={8}>
      {/* Portada: el único momento de la app que se permite ser grande. */}
      <VStack gap={3} hAlign="center">
        <Heading
          level={1}
          type="display-2"
          justify="center"
          id={COMPOSE_HEADING_ID}>
          Amanuense
        </Heading>
        <VStack maxWidth={520} gap={2}>
          <Text type="large" color="secondary" justify="center">
            Corrector de textos literarios
          </Text>
          <Text type="body" color="secondary" justify="center" textWrap="pretty">
            Pega el manuscrito o sube el archivo. Te lo devolvemos limpio: sin
            marcas, sin anotaciones, listo para seguir escribiendo.
          </Text>
        </VStack>
      </VStack>

      <Divider />

      <VStack gap={4}>
        <SegmentedControl
          label="Cómo quieres entregar el texto"
          value={source}
          onChange={(value) => onSourceChange(value as Source)}
          isDisabled={isWorking}>
          <SegmentedControlItem value="write" label="Escribir o pegar" />
          <SegmentedControlItem value="upload" label="Subir archivo" />
        </SegmentedControl>

        {source === 'write' ? (
          <TextArea
            label="Manuscrito"
            isLabelHidden
            value={text}
            onChange={onTextChange}
            placeholder="Aquí, el texto…"
            rows={16}
            size="lg"
            width="100%"
            isReadOnly={isWorking}
            statusVariant="detached"
            status={error ? {type: 'error', message: error} : undefined}
          />
        ) : (
          <FileInput
            label="Manuscrito"
            isLabelHidden
            mode="dropzone"
            accept={ACCEPTED}
            maxSize={MAX_FILE_BYTES}
            value={file}
            onChange={(value) => onFileChange((value as File | null) ?? null)}
            placeholder="Arrastra el archivo o haz clic para elegirlo"
            description="Texto plano o Markdown, hasta 2 MB."
            isDisabled={isWorking}
            width="100%"
            statusVariant="detached"
            status={
              error
                ? {type: 'error', message: error}
                : file && !isEmpty
                  ? {type: 'success', message: `${summary} en cola.`}
                  : undefined
            }
          />
        )}

        <HStack justify="between" vAlign="center" gap={3} wrap="wrap">
          <Text type="supporting">{summary}</Text>
          <HStack gap={2}>
            <Button
              label="Vaciar"
              variant="ghost"
              onClick={onClear}
              isDisabled={isEmpty || isWorking}
            />
            <Button
              label="Corregir"
              variant="primary"
              onClick={onSubmit}
              isLoading={isWorking}
              isDisabled={isEmpty}
            />
          </HStack>
        </HStack>
      </VStack>
    </VStack>
  );
}
