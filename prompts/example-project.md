# Example project profile

This text is injected into the F10 system prompt as **project context**, so the LLM
knows what you are building when it turns your dictation into a prompt. Replace
everything below with your own project's details.

## Project
A learning platform for high-school students.

## Stack
React + TypeScript frontend, Supabase (Postgres + Auth) backend, deployed on Vercel.

## Conventions
- Components in `src/components`, one folder per component.
- Use the existing design tokens in `src/styles/tokens.css`; do not introduce new colors.
- Tests with Vitest, colocated as `*.test.tsx`.

## When building a prompt for a coding AI
- Reference the files/areas above by name when relevant.
- Prefer the smallest change that satisfies the request.
