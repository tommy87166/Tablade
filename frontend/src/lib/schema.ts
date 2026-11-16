/**
 * 依 pydantic 產生的 JSON Schema 決定表單欄位（DESIGN.md §16.5）。
 * 前端沒有任何步驟專屬的表單程式碼：新增步驟只要寫 Python，表單自動出現。
 */
import type { Issue, JsonSchema } from '../bridge'

export const HIDDEN_FIELDS = new Set(['id', 'type', 'enabled'])

export type FieldKind =
  | 'column'
  | 'columns'
  | 'new_columns'
  | 'regex'
  | 'enum'
  | 'boolean'
  | 'integer'
  | 'number'
  | 'string'
  | 'conditions'
  | 'source_files'
  | 'unknown'

export function resolve(schema: JsonSchema, root: JsonSchema): JsonSchema {
  let current = schema
  while (current.$ref) {
    const name = current.$ref.split('/').pop()!
    current = root.$defs?.[name] ?? {}
  }
  return current
}

export function fieldKind(prop: JsonSchema, root: JsonSchema): FieldKind {
  const schema = resolve(prop, root)
  if (
    schema.ui === 'column' ||
    schema.ui === 'columns' ||
    schema.ui === 'new_columns' ||
    schema.ui === 'regex' ||
    schema.ui === 'source_files'
  ) {
    return schema.ui
  }
  if (schema.enum) return 'enum'
  if (schema.type === 'array' && schema.items?.discriminator) return 'conditions'
  if (schema.type === 'boolean') return 'boolean'
  if (schema.type === 'integer') return 'integer'
  if (schema.type === 'number') return 'number'
  if (schema.type === 'string') return 'string'
  return 'unknown'
}

export function editableFields(schema: JsonSchema): [string, JsonSchema][] {
  return Object.entries(schema.properties ?? {}).filter(([name]) => !HIDDEN_FIELDS.has(name))
}

export interface Variant {
  op: string
  label: string
  schema: JsonSchema
}

/** 條件陣列（discriminated union）的每一種 op 與對應的子 schema。 */
export function variants(arraySchema: JsonSchema, root: JsonSchema): Variant[] {
  const items = arraySchema.items
  const disc = items?.discriminator
  if (!disc) return []
  return Object.entries(disc.mapping).map(([op, ref]) => {
    const schema = resolve({ $ref: ref }, root)
    const labels = schema.properties?.[disc.propertyName]?.ui_labels ?? {}
    return { op, label: labels[op] ?? op, schema }
  })
}

export function defaultFor(prop: JsonSchema, root: JsonSchema): unknown {
  const schema = resolve(prop, root)
  if ('default' in schema) return schema.default
  switch (fieldKind(schema, root)) {
    case 'boolean':
      return false
    case 'columns':
    case 'new_columns':
    case 'conditions':
    case 'source_files':
      return []
    default:
      return undefined
  }
}

/** pydantic 的錯誤路徑（例如 conditions[0].value）對應到欄位。 */
export function issuesAt(issues: Issue[], path: string): Issue[] {
  return issues.filter((i) => i.path === path || i.path.startsWith(`${path}[`) || i.path.startsWith(`${path}.`))
}
