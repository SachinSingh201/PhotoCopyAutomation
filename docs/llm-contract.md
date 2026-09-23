# LLM NLP Structured Output Contract

## 1. Role of LLM
The LLM acts strictly as an Natural Language Processing (NLP) component that maps freeform customer instructions into a strongly-typed JSON schema.

## 2. Pydantic Schema Definition

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "LLMInterpretationResult",
  "type": "object",
  "properties": {
    "file_rules": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file_reference": {
            "type": "integer",
            "description": "1-based sequence index of the referenced file"
          },
          "color_mode": {
            "type": "string",
            "enum": ["bw", "color"]
          },
          "default_sides": {
            "type": "string",
            "enum": ["single", "double"]
          },
          "rules": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "page_start": { "type": "integer", "minimum": 1 },
                "page_end": { "type": ["integer", "null"] },
                "sides": { "type": "string", "enum": ["single", "double"] },
                "color": { "type": ["string", "null"], "enum": ["bw", "color", null] }
              },
              "required": ["page_start", "sides"]
            }
          }
        },
        "required": ["file_reference"]
      }
    },
    "ambiguity": {
      "type": "boolean"
    },
    "clarification_question": {
      "type": ["string", "null"]
    }
  },
  "required": ["file_rules", "ambiguity"]
}
```

## 3. Security Guardrails
- **Prompt Isolation**: User documents and customer messages are passed inside delimited data blocks with explicit system directives that content is untrusted data and must never be interpreted as system instructions.
- **Backend Range Validation**: The backend checks extracted ranges against actual physical page counts from `PyMuPDF`. The LLM's output is untrusted until validated by the backend.
