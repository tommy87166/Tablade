/**
 * 依 pydantic 產生的 JSON Schema 決定表單欄位（DESIGN.md §16.5）。
 * 前端沒有任何步驟專屬的表單程式碼：新增步驟只要寫 Python，表單自動出現。
 */
import type { Issue, JsonSchema } from '../bridge'

export const HIDDEN_FIELDS = new Set(['id', 'type', 'enabled'])

export type FieldKind =
  | 'column'
  | 'columns'
  | 'column_positions'
  | 'column_order'
  | 'rename_map'
  | 'int_list'
  | 'new_columns'
  | 'regex'
  | 'enum'
  | 'boolean'
  | 'integer'
  | 'number'
  | 'string'
  | 'conditions'
  | 'objects'
  | 'source_files'
  | 'stage'
  | 'right_column'
  | 'right_columns'
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
    schema.ui === 'column_positions' ||
    schema.ui === 'column_order' ||
    schema.ui === 'rename_map' ||
    schema.ui === 'int_list' ||
    schema.ui === 'new_columns' ||
    schema.ui === 'regex' ||
    schema.ui === 'source_files' ||
    schema.ui === 'stage' ||
    schema.ui === 'right_column' ||
    schema.ui === 'right_columns'
  ) {
    return schema.ui
  }
  if (schema.enum) return 'enum'
  if (schema.type === 'array' && schema.items?.discriminator) return 'conditions'
  // 物件的陣列（例如取代值的「找 → 換成」）：每一項依子 schema 畫欄位
  if (schema.type === 'array' && schema.items?.$ref && resolve(schema.items, root).properties) return 'objects'
  if (schema.type === 'boolean') return 'boolean'
  if (schema.type === 'integer') return 'integer'
  if (schema.type === 'number') return 'number'
  if (schema.type === 'string') return 'string'
  return 'unknown'
}

export function editableFields(schema: JsonSchema): [string, JsonSchema][] {
  return Object.entries(schema.properties ?? {}).filter(([name]) => !HIDDEN_FIELDS.has(name))
}

/** ``ui_show_if`` 的條件都成立（其他參數是指定的值、或是陣列裡的其中一個；沒填時用預設值）才顯示這個欄位。 */
export function visible(prop: JsonSchema, values: Record<string, unknown>, root: JsonSchema): boolean {
  const when = resolve(prop, root).ui_show_if
  if (!when) return true
  return Object.entries(when).every(([name, expected]) => {
    const other = root.properties?.[name]
    const current = name in values ? values[name] : other ? resolve(other, root).default : undefined
    return Array.isArray(expected) ? expected.includes(current) : current === expected
  })
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
    case 'column_positions':
    case 'column_order':
    case 'int_list':
    case 'new_columns':
    case 'conditions':
    case 'objects':
    case 'source_files':
      return []
    case 'rename_map':
      return {}
    default:
      return undefined
  }
}

/** pydantic 的錯誤路徑（例如 conditions[0].value）對應到欄位。 */
export function issuesAt(issues: Issue[], path: string): Issue[] {
  return issues.filter((i) => i.path === path || i.path.startsWith(`${path}[`) || i.path.startsWith(`${path}.`))
}
