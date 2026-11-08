// Run the frozen official TOON normalizer, using Node's TypeScript stripping.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const { compareAnswers } = await import(pathToFileURL(process.argv[2]).href);
const request = JSON.parse(readFileSync(0, 'utf8'));
const match = request.references.some(reference => compareAnswers(
  request.actual, reference, request.answer_type ?? 'string', request.options ?? {},
).match);
process.stdout.write(JSON.stringify({ match }));
