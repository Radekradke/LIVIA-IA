/**
 * Linha de cota — a assinatura visual do projeto.
 * Mede um vão e o nomeia, como a anotação de um desenho técnico.
 * O valor é conteúdo real (tempo, extensão, distância), nunca enfeite.
 */
export function DimensionLine({
  value,
  label,
  className,
}: {
  value: string;
  /** Descrição acessível do que está sendo medido. */
  label: string;
  className?: string;
}) {
  return (
    <div className={className ? `dim ${className}` : "dim"} role="img" aria-label={`${label}: ${value}`}>
      <span className="dim-cap" aria-hidden="true" />
      <span className="dim-run" aria-hidden="true" />
      <span className="dim-value" aria-hidden="true">
        {value}
      </span>
    </div>
  );
}
