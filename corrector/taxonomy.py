"""Error taxonomy shared by the corruptor, the corrector and the metrics.

Every entry pairs a fine-grained error type with one of the four categories the
pipeline treats differently: the mechanical ones can be applied on their own,
style can only ever be a suggestion.
"""

ORTOGRAFIA = "ortografía"
GRAMATICA = "gramática"
ORTOTIPOGRAFIA = "ortotipografía"
ESTILO = "estilo"

ERROR_TYPES = {
    "tilde": ORTOGRAFIA,
    "tilde_diacritica": ORTOGRAFIA,
    "ortografia_h": ORTOGRAFIA,
    "ortografia_bv": ORTOGRAFIA,
    "homofono": ORTOGRAFIA,
    "concordancia_genero": GRAMATICA,
    "concordancia_numero": GRAMATICA,
    "dequeismo": GRAMATICA,
    "queismo": GRAMATICA,
    "laismo": GRAMATICA,
    "loismo": GRAMATICA,
    "verbo_2sg": GRAMATICA,
    "raya_dialogo": ORTOTIPOGRAFIA,
    "comillas": ORTOTIPOGRAFIA,
    "signo_apertura": ORTOTIPOGRAFIA,
    "espaciado": ORTOTIPOGRAFIA,
    "mayuscula": ORTOTIPOGRAFIA,
    "estilo": ESTILO,
    "otro": ESTILO,
}
