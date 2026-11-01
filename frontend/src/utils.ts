import { AxiosError } from "axios"

import i18n from "@/i18n"

/**
 * Translate machine-readable server messages ("errors.*", "queue.reason.*").
 * A dynamic detail (filename, count, raw error) is appended after "|", e.g.
 * "errors.duplicate_file|scan.pdf" → translated text with the filename kept.
 * Plain (non-key) messages are returned unchanged.
 */
export const translateServerMessage = (raw: string): string => {
  const separator = raw.indexOf("|")
  const key = separator === -1 ? raw : raw.slice(0, separator)
  let detail = separator === -1 ? "" : raw.slice(separator + 1)
  if (!key.startsWith("errors.") && !key.startsWith("queue.reason.")) {
    return raw
  }
  if (key === "queue.reason.export_failed" && !detail) {
    return i18n.t("queue.reason.export_failed_bare")
  }
  if (!detail) detail = i18n.t("common.unknown")
  return i18n.t(key, { detail, defaultValue: raw })
}

function extractErrorMessage(err: Error): string {
  if (err instanceof AxiosError) {
    const errDetail = (err.response?.data as any)?.detail
    if (Array.isArray(errDetail) && errDetail.length > 0) {
      return errDetail[0].msg
    }
    if (typeof errDetail === "string") {
      return translateServerMessage(errDetail)
    }
    return err.message
  }
  return i18n.t("common.unknownError")
}

export const handleError = function (this: (msg: string) => void, err: Error) {
  const errorMessage = extractErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}
