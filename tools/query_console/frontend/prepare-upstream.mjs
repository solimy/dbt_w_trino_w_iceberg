import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [, , sourceDir] = process.argv

function patchPinnedFile(path, replacements) {
    const file = join(sourceDir, path)
    let source = readFileSync(file, 'utf8')
    for (const [before, after, count = 1] of replacements) {
        if (source.split(before).length !== count + 1) {
            throw new Error(`Unexpected upstream source in ${path}: ${before}`)
        }
        source = source.replaceAll(before, after)
    }
    writeFileSync(file, source)
}

// The proxy supplies the signed-in user for every statement request.
patchPinnedFile('src/AsyncTrinoClient.tsx', [
    ["import { Logger } from './utils/logger'", "import { Logger } from './utils/logger'\nimport { fetchTrino } from '../console/session'"],
    ['fetch(', 'fetchTrino(', 3],
    ["                headers: {\n                    'X-Trino-User': 'system',\n                },\n", '', 2],
    ["const headers: Record<string, string> = {\n            'X-Trino-User': 'system',\n        }", 'const headers: Record<string, string> = {}'],
])

// Discover only the catalogs visible to the signed-in user under Trino policy.
patchPinnedFile('src/sql/SchemaProvider.ts', [
    ['new Catalog(results[i][0], results[i][1])', "new Catalog(results[i][0], results[i][0] === 'system' ? 'system' : '')"],
    ['select catalog_name, connector_name from system.metadata.catalogs', 'SHOW CATALOGS'],
])
