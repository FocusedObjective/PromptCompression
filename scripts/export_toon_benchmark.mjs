// Export source data/questions only. This never imports an AI provider or calls a model.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2]);
const load = file => import(pathToFileURL(resolve(root, file)).href);
const { ACCURACY_DATASETS } = await load('benchmarks/src/datasets.ts');
const { generateQuestions } = await load('benchmarks/src/questions/index.ts');
const normalizer = readFileSync(resolve(root, 'benchmarks/src/normalize.ts'), 'utf8');
const cases = [];
const skipped = [];
for (const question of generateQuestions()) {
  const dataset = ACCURACY_DATASETS.find(d => d.name === question.dataset);
  // These tasks mutate encoded text and cannot be represented by intact source JSON.
  if (!dataset || (dataset.corruption && dataset.corruption.kind !== 'control')) {
    skipped.push(question.id);
    continue;
  }
  cases.push({
    id: `toon/${question.dataset}/${question.id}`,
    dataset: `toon/${question.dataset}`,
    context: JSON.stringify(dataset.data, null, 2),
    context_format: 'json',
    question: question.prompt,
    instruction: 'Answer using only the supplied data. The data may use JSON or TOON notation. '
      + 'Provide only the value, without explanation. For numbers, output digits without commas, '
      + 'currency symbols or units. For dates and field names, use the exact string from the data. '
      + 'For lists, output comma-separated values without spaces.',
    answers: [question.groundTruth],
    metric: 'toon',
    scoring: {
      answer_type: question.answerType ?? 'string',
      options: question.normalizationOptions ?? {},
    },
  });
}
process.stdout.write(JSON.stringify({ cases, normalizer, skipped }));
