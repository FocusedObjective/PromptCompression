export const REGEX_TOKEN_ESTIMATOR = "regex:unicode-word-or-non-space";

export interface TokenEstimate {
  count: number;
  estimator: string;
  tokenizerBacked: boolean;
}

export function estimateRegexTokens(text: string): TokenEstimate {
  let count = 0;
  let inWord = false;

  for (const char of text) {
    if (/\s/u.test(char)) {
      inWord = false;
      continue;
    }

    if (/[\p{L}\p{N}]/u.test(char)) {
      if (!inWord) {
        count += 1;
        inWord = true;
      }
      continue;
    }

    count += 1;
    inWord = false;
  }

  return {
    count,
    estimator: REGEX_TOKEN_ESTIMATOR,
    tokenizerBacked: false
  };
}

export function mergeTokenEstimators(names: string[]): string {
  const unique = [...new Set(names.filter(Boolean))].sort();
  if (unique.length === 0) {
    return REGEX_TOKEN_ESTIMATOR;
  }
  if (unique.length === 1) {
    return unique[0];
  }
  return `mixed:${unique.join(",")}`;
}

export function compressionRatio(inputTokens: number, outputTokens: number): number {
  return outputTokens === 0 ? 0 : inputTokens / outputTokens;
}

export function reduction(inputTokens: number, outputTokens: number): number {
  if (inputTokens <= 0) {
    return 0;
  }
  return Math.max(0, 1 - outputTokens / inputTokens);
}
