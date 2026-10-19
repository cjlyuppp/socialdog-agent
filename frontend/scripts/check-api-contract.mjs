// Compare independently maintained TypeScript responses with the exported FastAPI schema.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const api = JSON.parse(readFileSync(process.argv[2] ?? "../docs/openapi.json", "utf8"));
const source = ts.createSourceFile("types.ts", readFileSync(new URL("../src/api/types.ts", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true);
const definitions = new Map(source.statements.filter(node => ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)).map(node => [node.name.text, node]));
const schemas = api.components.schemas;
const resolve = schema => schema.$ref ? resolve(schemas[schema.$ref.split("/").at(-1)]) : schema;

function fields(name) {
  const node = definitions.get(name);
  assert.ok(node && ts.isInterfaceDeclaration(node), `Missing interface ${name}`);
  return Object.assign({}, ...((node.heritageClauses ?? []).flatMap(clause => clause.types.map(type => fields(type.expression.getText(source))))),
    Object.fromEntries(node.members.filter(ts.isPropertySignature).map(member => [member.name.getText(source), member.type])));
}

function kinds(node) {
  if (ts.isUnionTypeNode(node)) return new Set(node.types.flatMap(type => [...kinds(type)]));
  if (ts.isParenthesizedTypeNode(node)) return kinds(node.type);
  if (ts.isArrayTypeNode(node)) return new Set(["array"]);
  if (ts.isTypeLiteralNode(node) || ts.isIntersectionTypeNode(node)) return new Set(["object"]);
  if (ts.isTypeReferenceNode(node)) {
    const name = node.typeName.getText(source);
    const definition = definitions.get(name);
    return definition && ts.isTypeAliasDeclaration(definition) ? kinds(definition.type) : new Set([name === "Array" ? "array" : "object"]);
  }
  if (ts.isLiteralTypeNode(node)) {
    if (node.literal.kind === ts.SyntaxKind.NullKeyword) return new Set(["null"]);
    return new Set([ts.isStringLiteral(node.literal) ? "string" : ts.isNumericLiteral(node.literal) ? "number" : "boolean"]);
  }
  const name = { [ts.SyntaxKind.StringKeyword]: "string", [ts.SyntaxKind.NumberKeyword]: "number", [ts.SyntaxKind.BooleanKeyword]: "boolean" }[node.kind];
  assert.ok(name, `Unsupported TypeScript contract: ${node.getText(source)}`);
  return new Set([name]);
}

function schemaKinds(schema) {
  schema = resolve(schema);
  if (schema.anyOf) return new Set(schema.anyOf.flatMap(item => [...schemaKinds(item)]));
  return new Set([schema.type === "integer" ? "number" : schema.type]);
}

function literals(node) {
  if (ts.isTypeReferenceNode(node)) {
    const definition = definitions.get(node.typeName.getText(source));
    return definition && ts.isTypeAliasDeclaration(definition) ? literals(definition.type) : null;
  }
  if (ts.isLiteralTypeNode(node)) return [node.literal.kind === ts.SyntaxKind.NullKeyword ? null : node.literal.kind === ts.SyntaxKind.TrueKeyword ? true : node.literal.kind === ts.SyntaxKind.FalseKeyword ? false : ts.isStringLiteral(node.literal) ? node.literal.text : Number(node.literal.getText(source))];
  if (ts.isUnionTypeNode(node)) {
    const entries = node.types.map(literals);
    return entries.every(Boolean) ? entries.flat() : null;
  }
  return null;
}

let count = 0;
const responses = ["Quota", "Me", "AdminUser", "InviteInfo", "InviteLink", "DataStatus", "Session", "SessionDetail", "Evidence", "Grounding", "AgentResult", "Turn", "ProgressEvent", "Job", "ReferenceMeta", "ReferenceQuery", "ReferenceRow", "ReferenceResult", "VersionedQuestion", "RatingScores", "EvalAttempt", "Rating", "UsageLine", "UsageReport", "SiteSettings", "FeedbackIssue", "ResearchIdea", "Assignment", "AssignmentProgress", "BankVersion", "ResearcherSummary", "ModelOption", "ModelCatalog", "ReferenceWindow", "IdeaField", "ResearchPolicy", "BankDraft", "BankDetail", "BankWorkspace", "ReviewMember"];
for (const name of responses) {
  const schema = schemas[name + "-Output"] ?? schemas[name];
  assert.ok(schema, `Missing API response schema ${name}`);
  const front = fields(name), back = resolve(schema).properties;
  assert.deepEqual(Object.keys(front).sort(), Object.keys(back).sort(), `${name}: fields differ`);
  for (const [key, type] of Object.entries(front)) {
    assert.deepEqual([...kinds(type)].sort(), [...schemaKinds(back[key])].sort(), `${name}.${key}: JSON types differ`);
    const values = literals(type), backend = resolve(back[key]);
    if (values && backend.enum) assert.deepEqual([...values].sort(), [...backend.enum].sort(), `${name}.${key}: allowed values differ`);
    if (values && "const" in backend) assert.deepEqual(values, [backend.const], `${name}.${key}: fixed value differs`);
    count++;
  }
}
console.log(`API response contract: ${responses.length} interfaces, ${count} fields verified.`);
