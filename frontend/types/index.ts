// Convenience aliases over the OpenAPI-generated types (regenerate with `npm run gen:api`).
import type { components } from "./api";

type S = components["schemas"];

export type UserRead = S["UserRead"];
export type Role = UserRead["role"];
export type OrganizationRead = S["OrganizationRead"];
export type MeResponse = S["MeResponse"];
export type TokenResponse = S["TokenResponse"];
export type DocumentRead = S["DocumentRead"];
export type DocumentDetail = S["DocumentDetail"];
export type DocumentStatus = DocumentRead["status"];
export type DocumentVisibility = DocumentRead["visibility"];
export type FileType = DocumentRead["file_type"];
export type IngestionJobRead = S["IngestionJobRead"];
export type UploadResponse = S["UploadResponse"];
export type UploadResult = S["UploadResult"];
export type ChunkRead = S["ChunkRead"];
export type ChunkPage = S["ChunkPage"];
export type CategoryRead = S["CategoryRead"];
export type ConversationRead = S["ConversationRead"];
export type ConversationDetail = S["ConversationDetail"];
export type MessageRead = S["MessageRead"];
export type CitationRead = S["CitationRead"];
export type SourceRead = S["SourceRead"];
export type SearchResponse = S["SearchResponse"];
export type SearchHit = S["SearchHit"];

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type Permission = MeResponse["permissions"][number];
