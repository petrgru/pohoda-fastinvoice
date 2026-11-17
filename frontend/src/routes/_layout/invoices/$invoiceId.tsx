import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createFileRoute,
  Link as RouterLink,
  useNavigate,
} from "@tanstack/react-router"
import { AxiosError } from "axios"
import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Plus,
  RotateCcw,
  X,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import {
  CompaniesService,
  type InvoiceDuplicateHint,
  InvoicesService,
} from "@/client"
import { AddCompanyForInvoice } from "@/components/Invoices/AddCompanyForInvoice"
import { formatPrice } from "@/components/Invoices/columns"
import { IdCandidates } from "@/components/Invoices/IdCandidates"
import { InvoiceActionsMenu } from "@/components/Invoices/InvoiceActionsMenu"
import { InvoicePreview } from "@/components/Invoices/InvoicePreview"
import { StateBadge } from "@/components/Invoices/StateBadge"
import { recalcVatRow, type VatField } from "@/components/Invoices/vatMath"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useCustomToast from "@/hooks/useCustomToast"
import { intlLocale } from "@/i18n"
import { handleError, translateServerMessage } from "@/utils"

/** 409 raised when the proposed account deviates from document/supplier history */
function isAccountConfirmConflict(err: unknown): boolean {
  return (
    err instanceof AxiosError &&
    err.response?.status === 409 &&
    (err.response?.data as { detail?: unknown } | undefined)?.detail ===
      "errors.account_changed_confirm"
  )
}

const PAYMENT_METHODS = ["splatna", "zaplacena", "inkasem", "hotove"] as const

/** "1 234,56" → "1234.56" for the API's Decimal fields. */
function amountToDecimal(value: string): string {
  const cleaned = (value || "").replace(/\s/g, "").replace(",", ".")
  const parsed = Number(cleaned)
  return Number.isFinite(parsed) ? parsed.toFixed(2) : "0.00"
}

type FormData = {
  invoice_number?: string
  supplier_name?: string
  supplier_ico?: string
  supplier_dic?: string
  supplier_bank_account?: string
  supplier_iban?: string
  supplier_bank_accounts?: { account: string; iban: string }[]
  supplier_email?: string
  issue_date?: string
  due_date?: string
  delivery_date?: string
  kh_date?: string
  payment_method?: string
  vat_rows?: {
    vat_rate: string
    base_price: string
    tax_price: string
    total_price: string
    currency: string
  }[]
  total_price?: string
  total_vat?: string
  total_without_vat?: string
}

export const Route = createFileRoute("/_layout/invoices/$invoiceId")({
  component: InvoiceDetail,
  head: () => ({ meta: [{ title: "Doklad – Pohoda FastInvoice" }] }),
})

function InvoiceDetail() {
  const { t } = useTranslation()
  const { invoiceId } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const { data: invoice } = useQuery({
    queryKey: ["invoice", invoiceId],
    queryFn: async () =>
      (await InvoicesService.getInvoice({ path: { invoice_id: invoiceId } }))
        .data,
    // background parsing finishes after the page was opened – poll until the
    // invoice reaches a final state so the UI updates without a reload
    refetchInterval: (query) =>
      query.state.data?.state === "uploaded" ||
      query.state.data?.state === "parsing"
        ? 2000
        : false,
  })
  const formSchema = useMemo(
    () =>
      z
        .object({
          invoice_number: z.string().optional(),
          supplier_name: z.string().optional(),
          supplier_ico: z
            .string()
            .regex(/^\d{8}$/, t("invoice.icoInvalid"))
            .or(z.literal(""))
            .optional(),
          supplier_dic: z.string().optional(),
          supplier_bank_account: z.string().optional(),
          supplier_iban: z.string().optional(),
          supplier_bank_accounts: z
            .array(z.object({ account: z.string(), iban: z.string() }))
            .optional(),
          supplier_email: z.string().optional(),
          issue_date: z.string().optional(),
          due_date: z.string().optional(),
          delivery_date: z.string().optional(),
          kh_date: z.string().optional(),
          payment_method: z.string().optional(),
          vat_rows: z
            .array(
              z.object({
                vat_rate: z.string(),
                base_price: z
                  .string()
                  .regex(/^\d{1,12}([.,]\d{1,2})?$/, t("invoice.errAmount"))
                  .or(z.literal("")),
                tax_price: z
                  .string()
                  .regex(/^\d{1,12}([.,]\d{1,2})?$/, t("invoice.errAmount"))
                  .or(z.literal("")),
                total_price: z
                  .string()
                  .regex(/^\d{1,12}([.,]\d{1,2})?$/, t("invoice.errAmount"))
                  .or(z.literal("")),
                currency: z.string(),
              }),
            )
            .optional(),
          total_price: z.string().optional(),
          total_vat: z.string().optional(),
          total_without_vat: z.string().optional(),
        })
        // a tax document must carry the three key dates (the backend
        // enforces the same on save); proforma and receipts are exempt
        .superRefine((value, ctx) => {
          if (invoice?.document_type && invoice.document_type !== "invoice")
            return
          for (const field of ["issue_date", "due_date", "delivery_date"]) {
            if (!value[field as "issue_date"]) {
              ctx.addIssue({
                code: "custom",
                message: t("invoice.dateRequired"),
                path: [field],
              })
            }
          }
        }),
    [t, invoice?.document_type],
  )

  const { data: companies } = useQuery({
    queryKey: ["companies"],
    queryFn: async () =>
      (await CompaniesService.listCompanies({ query: { limit: 200 } })).data,
  })
  const parsingInProgress =
    invoice?.state === "uploaded" || invoice?.state === "parsing"

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    values: {
      invoice_number: invoice?.invoice_number ?? "",
      supplier_name: invoice?.supplier_name ?? "",
      supplier_ico: invoice?.supplier_ico ?? "",
      supplier_dic: invoice?.supplier_dic ?? "",
      supplier_bank_account: invoice?.supplier_bank_account ?? "",
      supplier_iban: invoice?.supplier_iban ?? "",
      supplier_bank_accounts: (invoice?.supplier_bank_accounts ?? []).map(
        (row) => ({
          account: row.account ?? "",
          iban: row.iban ?? "",
        }),
      ),
      supplier_email: invoice?.supplier_email ?? "",
      issue_date: invoice?.issue_date ?? "",
      due_date: invoice?.due_date ?? "",
      delivery_date: invoice?.delivery_date ?? "",
      kh_date: invoice?.kh_date ?? "",
      payment_method: invoice?.payment_method ?? "",
      vat_rows: (invoice?.vat_items ?? []).map((item) => ({
        vat_rate: item.vat_rate ?? "",
        base_price: item.base_price ?? "",
        tax_price: item.tax_price ?? "",
        total_price: item.total_price ?? "",
        currency: item.currency ?? "",
      })),
      total_price: invoice?.total_price ?? "",
      total_vat: invoice?.total_vat ?? "",
      total_without_vat: invoice?.total_without_vat ?? "",
    },
  })

  // editing any cell of a VAT line recalculates the rest of the line
  const onVatCellChange = (
    index: number,
    cell: VatField,
    onChange: (value: string) => void,
    value: string,
  ) => {
    onChange(value)
    const current = (form.getValues("vat_rows") ?? [])[index]
    if (!current) return
    form.setValue(
      `vat_rows.${index}`,
      recalcVatRow({ ...current, [cell]: value }, cell),
      { shouldDirty: true },
    )
  }
  const vatRowFields = useFieldArray({
    control: form.control,
    name: "vat_rows",
  })
  const supplierAccounts = useFieldArray({
    control: form.control,
    name: "supplier_bank_accounts",
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] })
    queryClient.invalidateQueries({ queryKey: ["invoices"] })
    queryClient.invalidateQueries({ queryKey: ["queue"] })
    // companies created from this invoice must show up in the header switcher
    queryClient.invalidateQueries({ queryKey: ["companies"] })
  }

  // form awaiting the user's "the account is really correct" confirmation
  // the account panel is collapsed unless a mismatch was detected; a manual
  // toggle wins over the automatic choice
  const [accountsOpenOverride, setAccountsOpenOverride] = useState<
    boolean | null
  >(null)
  const [accountConfirmData, setAccountConfirmData] = useState<FormData | null>(
    null,
  )
  // resolving a document that is already approved elsewhere needs a decision
  const [duplicateDialogOpen, setDuplicateDialogOpen] = useState(false)

  const saveMutation = useMutation({
    mutationFn: ({ data, confirm }: { data: FormData; confirm: boolean }) => {
      const body: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(data)) {
        if (key === "vat_rows") {
          const rows = ((value as FormData["vat_rows"]) ?? [])
            .filter(
              (row) =>
                row.base_price.trim() ||
                row.tax_price.trim() ||
                row.vat_rate.trim(),
            )
            .map((row) => {
              const base = Number(amountToDecimal(row.base_price))
              const tax = Number(amountToDecimal(row.tax_price))
              return {
                vat_rate: row.vat_rate.trim() || null,
                base_price: amountToDecimal(row.base_price),
                tax_price: amountToDecimal(row.tax_price),
                total_price: row.total_price.trim()
                  ? amountToDecimal(row.total_price)
                  : (base + tax).toFixed(2),
                currency: row.currency.trim() || null,
              }
            })
          if (rows.length > 0 || (invoice?.vat_items?.length ?? 0) > 0) {
            body.vat_items = rows
          }
          continue
        }
        if (key === "supplier_bank_accounts") {
          body[key] = (
            (value as FormData["supplier_bank_accounts"]) ?? []
          ).filter((row) => row.account.trim() || row.iban.trim())
          continue
        }
        if (value !== undefined && value !== "") body[key] = value
      }
      if (confirm) body.confirm_account_change = true
      return InvoicesService.updateInvoice({
        path: { invoice_id: invoiceId },
        body: body as never,
      })
    },
    onSuccess: () => {
      showSuccessToast(t("invoice.saved"))
      invalidate()
      setAccountConfirmData(null)
    },
    onError: (err, { confirm }) => {
      // 409 = the account deviates from the document/supplier baseline:
      // show the tampering dialog, resend only after the user confirms
      if (!confirm && isAccountConfirmConflict(err)) {
        setAccountConfirmData(saveMutation.variables?.data ?? ({} as FormData))
        return
      }
      handleError.call(showErrorToast, err)
    },
  })

  const assignMutation = useMutation({
    mutationFn: (companyId: string) =>
      InvoicesService.assignCompany({
        path: { invoice_id: invoiceId },
        body: { recipient_company_id: companyId },
      }),
    onSuccess: () => {
      showSuccessToast(t("invoice.companyAssigned"))
      invalidate()
    },
    onError: handleError.bind(showErrorToast),
  })

  const approveMutation = useMutation({
    mutationFn: (deleteDuplicates: boolean) =>
      InvoicesService.approveInvoice({
        path: { invoice_id: invoiceId },
        body: deleteDuplicates ? { delete_duplicates: true } : undefined,
      }),
    onSuccess: () => {
      showSuccessToast(t("invoice.approved"))
      invalidate()
    },
    onError: handleError.bind(showErrorToast),
  })

  const unapproveMutation = useMutation({
    mutationFn: () =>
      InvoicesService.unapproveInvoice({ path: { invoice_id: invoiceId } }),
    onSuccess: () => {
      showSuccessToast(t("invoice.approvalRevoked"))
      invalidate()
    },
    onError: handleError.bind(showErrorToast),
  })

  if (!invoice) {
    return <Skeleton className="h-96 w-full" />
  }

  const editable = invoice.can_delete && !parsingInProgress
  const recipientName =
    invoice.recipient_name ??
    (invoice.recipient_ico
      ? `${t("invoice.recipientFallback")} (IČO ${invoice.recipient_ico})`
      : null)
  const accountsMismatch =
    invoice.account_matches_document === false ||
    invoice.account_matches_supplier === false
  const accountsOpen = accountsOpenOverride ?? accountsMismatch
  const accountSummary = (() => {
    const values = [
      form.watch("supplier_bank_account"),
      ...supplierAccounts.fields.map((row) => row.account),
    ]
      .map((value) => (value ?? "").trim())
      .filter(Boolean)
    if (values.length === 0) return t("invoice.accountsNone")
    return values.length > 1
      ? `${values[0]} (+${values.length - 1})`
      : values[0]
  })()
  // copies of the same document number from the same supplier; those already
  // approved for (or through) the export block confirming this one
  const duplicateDocs = invoice.duplicates ?? []
  const blockingDuplicates = duplicateDocs.filter((item) =>
    ["ready_for_export", "processing", "exported"].includes(item.state),
  )
  const duplicatesLocked = blockingDuplicates.some(
    (item) => item.state === "exported",
  )
  const companyAccounts = (
    invoice.company?.bank_accounts?.length
      ? invoice.company.bank_accounts.map((row) => row.account ?? row.iban)
      : [invoice.company?.bank_account]
  ).filter(Boolean)
  const vatRows = invoice.vat_items ?? []
  // the document names one of our other companies as the recipient
  const mismatchedCompany =
    (companies?.data ?? []).find(
      (item) =>
        invoice.recipient_ico &&
        item.ico === invoice.recipient_ico &&
        item.id !== invoice.recipient_company_id,
    ) ?? null
  const showVatCurrency =
    new Set(vatRows.map((item) => item.currency ?? invoice.currency ?? null))
      .size > 1
  // totals speak the invoice currency; foreign lines are informational only
  const vatSums = vatRows.reduce(
    (sums, item) => {
      if (
        invoice.currency &&
        item.currency &&
        item.currency !== invoice.currency
      ) {
        return sums
      }
      return {
        base: sums.base + Number(item.base_price ?? 0),
        tax: sums.tax + Number(item.tax_price ?? 0),
        total: sums.total + Number(item.total_price ?? 0),
      }
    },
    { base: 0, tax: 0, total: 0 },
  )

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => navigate({ to: "/invoices" })}
          >
            <ArrowLeft className="size-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              {invoice.invoice_number ?? invoice.original_filename}
            </h1>
            <p className="text-sm text-muted-foreground">
              {t("invoice.uploadedAt")}{" "}
              {new Date(invoice.created_at ?? "").toLocaleString(intlLocale())}{" "}
              · {invoice.original_filename}
            </p>
          </div>
          <StateBadge
            state={invoice.state}
            errorMessage={invoice.erp_error_message}
          />
        </div>
        <InvoiceActionsMenu
          invoice={invoice}
          onSuccess={() => navigate({ to: "/invoices" })}
        />
      </div>

      {(invoice.queue_reasons?.length ?? 0) > 0 && invoice.can_delete && (
        <Alert data-testid="queue-reasons-alert">
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("invoice.staysInQueue")}</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-4">
              {invoice.queue_reasons?.map((reason) => (
                <li key={reason}>{translateServerMessage(reason)}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {duplicateDocs.length > 0 && (
        <Alert variant="destructive" data-testid="duplicate-alert">
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("invoice.duplicateTitle")}</AlertTitle>
          <AlertDescription>
            {t("invoice.duplicateText")}
            <DuplicateList duplicates={duplicateDocs} />
          </AlertDescription>
        </Alert>
      )}

      {invoice.can_delete &&
        !invoice.company &&
        !parsingInProgress &&
        recipientName && (
          <Alert data-testid="recipient-alert">
            <Building2 className="size-4" />
            <AlertTitle>{t("invoice.recipientTitle")}</AlertTitle>
            <AlertDescription>
              {t("invoice.recipientText", { name: recipientName })}
            </AlertDescription>
          </Alert>
        )}

      {invoice.state === "exported" && (
        <Alert className="border-green-500/40 bg-green-500/10">
          <CheckCircle2 className="size-4" />
          <AlertTitle>{t("invoice.exportedTitle")}</AlertTitle>
          <AlertDescription>{t("invoice.exportedText")}</AlertDescription>
        </Alert>
      )}

      {invoice.parse_error && (
        <Alert variant="destructive">
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("invoice.parseErrorTitle")}</AlertTitle>
          <AlertDescription>
            {t("invoice.parseErrorText", { error: invoice.parse_error })}
          </AlertDescription>
        </Alert>
      )}

      {invoice.account_matches_document === false && (
        <Alert
          className="border-amber-500/50 bg-amber-500/10"
          data-testid="account-doc-warning"
        >
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("invoice.accountChangedDocTitle")}</AlertTitle>
          <AlertDescription>
            {t("invoice.accountChangedDocText", {
              document: invoice.document_bank_accounts?.join(" · ") ?? "—",
            })}
          </AlertDescription>
        </Alert>
      )}
      {invoice.account_matches_supplier === false && (
        <Alert
          className="border-amber-500/50 bg-amber-500/10"
          data-testid="account-supplier-warning"
        >
          <AlertTriangle className="size-4" />
          <AlertTitle>{t("invoice.accountChangedSupplierTitle")}</AlertTitle>
          <AlertDescription>
            {t("invoice.accountChangedSupplierText", {
              confirmed:
                invoice.supplier_confirmed_accounts?.join(" · ") ?? "—",
            })}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h2 className="font-semibold">{t("invoice.document")}</h2>
          <InvoicePreview
            invoiceId={invoice.id}
            contentType={invoice.content_type}
            filename={invoice.original_filename}
          />
        </div>

        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-3">
            <h2 className="font-semibold">{t("invoice.companySection")}</h2>
            <Select
              value={invoice.recipient_company_id ?? undefined}
              onValueChange={(value) => assignMutation.mutate(value)}
              disabled={!editable || assignMutation.isPending}
            >
              <SelectTrigger
                data-testid="company-select"
                className={
                  mismatchedCompany ? "ring-1 ring-destructive" : undefined
                }
              >
                <SelectValue placeholder={t("invoice.assignPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {(companies?.data ?? []).map((company) => (
                  <SelectItem key={company.id} value={company.id}>
                    {company.name} ({company.ico})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {mismatchedCompany && (
              <Alert
                variant="destructive"
                data-testid="recipient-mismatch-alert"
              >
                <AlertTriangle className="size-4" />
                <AlertTitle>
                  {t("invoice.recipientOtherCompanyTitle")}
                </AlertTitle>
                <AlertDescription className="flex flex-col items-start gap-2">
                  {t("invoice.recipientOtherCompanyText", {
                    name: mismatchedCompany.name,
                    ico: mismatchedCompany.ico,
                  })}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => assignMutation.mutate(mismatchedCompany.id)}
                    data-testid="recipient-switch-button"
                  >
                    {t("invoice.recipientSwitch")}
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            {invoice.company && (
              <p className="text-sm text-muted-foreground">
                {t("invoice.icoLabel")}: {invoice.company.ico} ·{" "}
                {t("invoice.dicLabel")}: {invoice.company.dic ?? "—"} ·{" "}
                {companyAccounts.join(" · ")}
              </p>
            )}
            {invoice.external_number && (
              <p
                className="text-sm text-muted-foreground"
                data-testid="external-number"
              >
                {t("invoice.externalNumber")}:{" "}
                <span className="font-mono">{invoice.external_number}</span>
              </p>
            )}
            {invoice.company?.address && (
              <p className="text-sm text-muted-foreground">
                {invoice.company.address}
              </p>
            )}
            {/* "add our company" must stay reachable even without recipient */}
            {invoice.can_delete && !invoice.company && (
              <div>
                <AddCompanyForInvoice
                  invoice={invoice}
                  disabled={parsingInProgress}
                  onSuccess={invalidate}
                />
              </div>
            )}
          </div>

          <Separator />

          <Form {...form}>
            <form
              onSubmit={form.handleSubmit((data) =>
                saveMutation.mutate({ data, confirm: false }),
              )}
              className="flex flex-col gap-4"
            >
              <h2 className="font-semibold">{t("invoice.accounting")}</h2>
              <div className="grid grid-cols-2 gap-3">
                <FormField
                  control={form.control}
                  name="invoice_number"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.invoiceNumber")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="supplier_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.supplier")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="supplier_ico"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.icoLabel")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                      {editable && !/^\d{8}$/.test(field.value ?? "") ? (
                        <IdCandidates
                          invoiceId={invoiceId}
                          kind="ico"
                          onPick={(value) =>
                            form.setValue("supplier_ico", value, {
                              shouldDirty: true,
                            })
                          }
                        />
                      ) : null}
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="supplier_dic"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.dicLabel")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                      {editable &&
                      !/^[A-Z]{2}\d{8,12}$/i.test(
                        (field.value ?? "").trim(),
                      ) ? (
                        <IdCandidates
                          invoiceId={invoiceId}
                          kind="dic"
                          onPick={(value) =>
                            form.setValue("supplier_dic", value, {
                              shouldDirty: true,
                            })
                          }
                        />
                      ) : null}
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="supplier_email"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.supplierEmail")}</FormLabel>
                      <FormControl>
                        <Input type="email" {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="issue_date"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.issueDate")}</FormLabel>
                      <FormControl>
                        <Input type="date" {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="due_date"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.dueDate")}</FormLabel>
                      <FormControl>
                        <Input type="date" {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                      {field.value === "" &&
                        form.watch("payment_method") === "splatna" && (
                          <p className="text-xs text-amber-600">
                            {t("invoice.dueDateMissing")}
                          </p>
                        )}
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="delivery_date"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.taxDate")}</FormLabel>
                      <FormControl>
                        <Input type="date" {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="kh_date"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.khDate")}</FormLabel>
                      <FormControl>
                        <Input type="date" {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="payment_method"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.paymentMethod")}</FormLabel>
                      <Select
                        value={field.value || undefined}
                        onValueChange={field.onChange}
                        disabled={!editable}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue placeholder="—" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {PAYMENT_METHODS.map((method) => (
                            <SelectItem key={method} value={method}>
                              {t(`payment.${method}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="total_price"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.total")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="total_vat"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.vat")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="total_without_vat"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.totalWithoutVat")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              {/* every account number in one collapsible panel; it opens on
                  its own when the document carries a different account */}
              <div className="rounded-md border" data-testid="accounts-panel">
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium"
                  data-testid="accounts-toggle"
                  aria-expanded={accountsOpen}
                  onClick={() => setAccountsOpenOverride(!accountsOpen)}
                >
                  {accountsOpen ? (
                    <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  )}
                  {accountsMismatch && (
                    <AlertTriangle
                      className="size-4 shrink-0 text-destructive"
                      data-testid="accounts-mismatch-icon"
                    />
                  )}
                  <span className="shrink-0">{t("invoice.accountsPanel")}</span>
                  {!accountsOpen && (
                    <span className="truncate text-xs font-normal text-muted-foreground">
                      {accountSummary}
                    </span>
                  )}
                </button>
                {accountsOpen && (
                  <div className="grid gap-3 border-t p-3">
                    <FormField
                      control={form.control}
                      name="supplier_bank_account"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("invoice.bankAccount")}</FormLabel>
                          <FormControl>
                            <Input
                              {...field}
                              disabled={!editable}
                              className={
                                accountsMismatch
                                  ? "border-destructive"
                                  : undefined
                              }
                              data-testid="supplier-bank-account-input"
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="supplier_iban"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t("invoice.iban")}</FormLabel>
                          <FormControl>
                            <Input
                              {...field}
                              disabled={!editable}
                              className={
                                accountsMismatch
                                  ? "border-destructive"
                                  : undefined
                              }
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <span className="text-sm font-medium">
                      {t("invoice.documentAccounts")}
                    </span>
                    {supplierAccounts.fields.map((row, index) => (
                      <div
                        key={row.id}
                        className="grid grid-cols-[1fr_1fr_auto] items-start gap-3"
                        data-testid="supplier-account-row"
                      >
                        <FormField
                          control={form.control}
                          name={`supplier_bank_accounts.${index}.account`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                {t("companyFields.account")}
                              </FormLabel>
                              <FormControl>
                                <Input
                                  placeholder={t(
                                    "companyFields.accountPlaceholder",
                                  )}
                                  {...field}
                                  disabled={!editable}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name={`supplier_bank_accounts.${index}.iban`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>{t("companyFields.iban")}</FormLabel>
                              <FormControl>
                                <Input
                                  placeholder={t(
                                    "companyFields.ibanPlaceholder",
                                  )}
                                  {...field}
                                  disabled={!editable}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="mt-7"
                          disabled={!editable}
                          onClick={() => supplierAccounts.remove(index)}
                          aria-label={t("companyFields.removeAccount")}
                          data-testid="remove-supplier-account-button"
                        >
                          <X className="size-4" />
                        </Button>
                      </div>
                    ))}
                    {editable && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="justify-self-start"
                        onClick={() =>
                          supplierAccounts.append({ account: "", iban: "" })
                        }
                        data-testid="add-supplier-account-button"
                      >
                        <Plus className="mr-2 size-4" />
                        {t("companyFields.addAccount")}
                      </Button>
                    )}
                  </div>
                )}
              </div>

              {invoice.vat_check_ok === false && (
                <p className="text-sm text-destructive">
                  {t("invoice.vatMismatch", {
                    diff: formatPrice(invoice.vat_difference),
                  })}
                </p>
              )}

              {editable && (
                <LoadingButton
                  type="submit"
                  loading={saveMutation.isPending}
                  data-testid="invoice-save-button"
                >
                  {t("invoice.saveFields")}
                </LoadingButton>
              )}
            </form>
          </Form>

          {((invoice.vat_items?.length ?? 0) > 0 || editable) && (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-semibold">{t("invoice.vatItems")}</h2>
                {(invoice.vat_rate_warnings ?? []).map((warning) => {
                  const [, country, rate] = warning.split("|")
                  return (
                    <span
                      key={warning}
                      className="flex items-center gap-1 rounded-md border border-amber-500/50 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-600 dark:text-amber-400"
                    >
                      <AlertTriangle className="size-3.5" />
                      {t("invoice.rateUnusual", { country, rate })}
                    </span>
                  )
                })}
              </div>
              {editable ? (
                <Form {...form}>
                  <div
                    className="flex flex-col gap-3"
                    data-testid="vat-items-editable"
                  >
                    {vatRowFields.fields.map((row, index) => (
                      <div
                        key={row.id}
                        className="grid grid-cols-[9rem_1fr_1fr_1fr_auto] items-start gap-2"
                        data-testid={`vat-row-${index}`}
                      >
                        <FormField
                          control={form.control}
                          name={`vat_rows.${index}.vat_rate`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>{t("invoice.vatRate")}</FormLabel>
                              <FormControl>
                                <Input
                                  placeholder="21%"
                                  {...field}
                                  data-testid={`vat-row-rate-${index}`}
                                  onChange={(e) =>
                                    onVatCellChange(
                                      index,
                                      "vat_rate",
                                      field.onChange,
                                      e.target.value,
                                    )
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name={`vat_rows.${index}.base_price`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>{t("invoice.vatBase")}</FormLabel>
                              <FormControl>
                                <Input
                                  placeholder="1000.00"
                                  {...field}
                                  data-testid={`vat-row-base-${index}`}
                                  onChange={(e) =>
                                    onVatCellChange(
                                      index,
                                      "base_price",
                                      field.onChange,
                                      e.target.value,
                                    )
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name={`vat_rows.${index}.tax_price`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>{t("invoice.vat")}</FormLabel>
                              <FormControl>
                                <Input
                                  placeholder="210.00"
                                  {...field}
                                  data-testid={`vat-row-tax-${index}`}
                                  onChange={(e) =>
                                    onVatCellChange(
                                      index,
                                      "tax_price",
                                      field.onChange,
                                      e.target.value,
                                    )
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name={`vat_rows.${index}.total_price`}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>{t("invoice.total")}</FormLabel>
                              <FormControl>
                                <Input
                                  placeholder="1210.00"
                                  {...field}
                                  data-testid={`vat-row-total-${index}`}
                                  onChange={(e) =>
                                    onVatCellChange(
                                      index,
                                      "total_price",
                                      field.onChange,
                                      e.target.value,
                                    )
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="mt-6"
                          onClick={() => vatRowFields.remove(index)}
                          data-testid={`vat-row-remove-${index}`}
                        >
                          <X className="size-4" />
                        </Button>
                      </div>
                    ))}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="self-start"
                      onClick={() =>
                        vatRowFields.append({
                          vat_rate: "21%",
                          base_price: "",
                          tax_price: "",
                          total_price: "",
                          currency: "",
                        })
                      }
                      data-testid="vat-row-add"
                    >
                      <Plus className="mr-2 size-4" />
                      {t("invoice.vatAddRate")}
                    </Button>
                  </div>
                </Form>
              ) : (
                <Table data-testid="vat-items-table">
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("invoice.vatRate")}</TableHead>
                      {showVatCurrency && (
                        <TableHead>{t("invoice.vatCurrency")}</TableHead>
                      )}
                      <TableHead className="text-right">
                        {t("invoice.vatBase")}
                      </TableHead>
                      <TableHead className="text-right">
                        {t("invoice.vat")}
                      </TableHead>
                      <TableHead className="text-right">
                        {t("invoice.total")}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {invoice.vat_items?.map((item) => (
                      <TableRow
                        key={`${item.vat_rate ?? ""}-${item.currency ?? ""}`}
                      >
                        <TableCell>{item.vat_rate ?? "—"}</TableCell>
                        {showVatCurrency && (
                          <TableCell>
                            {item.currency ?? invoice.currency}
                          </TableCell>
                        )}
                        <TableCell className="text-right">
                          {formatPrice(item.base_price)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatPrice(item.tax_price)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatPrice(item.total_price)}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="font-medium">
                      <TableCell>{t("invoice.vatTotal")}</TableCell>
                      {showVatCurrency && <TableCell />}
                      <TableCell className="text-right">
                        {formatPrice(vatSums.base.toFixed(2))}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatPrice(vatSums.tax.toFixed(2))}
                      </TableCell>
                      <TableCell className="text-right">
                        {formatPrice(vatSums.total.toFixed(2))}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              )}
            </div>
          )}

          <Separator />

          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              {invoice.can_delete &&
                (invoice.approved_at ? (
                  <LoadingButton
                    variant="outline"
                    loading={unapproveMutation.isPending}
                    onClick={() => unapproveMutation.mutate()}
                    data-testid="unapprove-button"
                  >
                    <RotateCcw className="mr-2 size-4" />
                    {t("invoice.cancelImport")}
                  </LoadingButton>
                ) : (
                  <LoadingButton
                    loading={approveMutation.isPending}
                    disabled={parsingInProgress}
                    onClick={() => {
                      if (blockingDuplicates.length > 0) {
                        setDuplicateDialogOpen(true)
                      } else {
                        approveMutation.mutate(false)
                      }
                    }}
                    data-testid="approve-button"
                  >
                    <CheckCircle2 className="mr-2 size-4" />
                    {t("invoice.confirmImport")}
                  </LoadingButton>
                ))}
            </div>
            {invoice.can_delete &&
              !invoice.approved_at &&
              parsingInProgress && (
                <p className="text-sm text-muted-foreground">
                  {t("invoice.approveAfterParse")}
                </p>
              )}
            {(invoice.uploaded_by || invoice.approved_by) && (
              <p
                className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground"
                data-testid="invoice-actors"
              >
                <span data-testid="invoice-uploaded-by">
                  {t("invoice.uploadedBy")}:{" "}
                  {invoice.uploaded_by?.full_name ||
                    invoice.uploaded_by?.email ||
                    "—"}
                </span>
                <span data-testid="invoice-approved-by">
                  {t("invoice.approvedBy")}:{" "}
                  {invoice.approved_by
                    ? `${invoice.approved_by.full_name || invoice.approved_by.email} · ${new Date(invoice.approved_at ?? 0).toLocaleDateString()}`
                    : t("invoice.approvalPending")}
                </span>
              </p>
            )}
          </div>
        </div>
      </div>

      <Dialog
        open={accountConfirmData !== null}
        onOpenChange={(open) => {
          if (!open) setAccountConfirmData(null)
        }}
      >
        <DialogContent data-testid="account-confirm-dialog">
          <DialogHeader>
            <DialogTitle>{t("invoice.accountDiffTitle")}</DialogTitle>
            <DialogDescription>
              {t("invoice.accountDiffText", {
                document: invoice.document_bank_accounts?.join(" · ") ?? "—",
                supplierPart: invoice.supplier_confirmed_accounts?.length
                  ? t("invoice.accountDiffSupplierPart", {
                      confirmed:
                        invoice.supplier_confirmed_accounts.join(" · "),
                    })
                  : "",
              })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              data-testid="account-confirm-cancel"
              onClick={() => setAccountConfirmData(null)}
            >
              {t("invoice.accountDiffKeep")}
            </Button>
            <LoadingButton
              loading={saveMutation.isPending}
              data-testid="account-confirm-save"
              onClick={() =>
                accountConfirmData &&
                saveMutation.mutate({
                  data: accountConfirmData,
                  confirm: true,
                })
              }
            >
              {t("invoice.accountDiffConfirm")}
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={duplicateDialogOpen} onOpenChange={setDuplicateDialogOpen}>
        <DialogContent data-testid="duplicate-dialog">
          <DialogHeader>
            <DialogTitle>{t("invoice.duplicateTitle")}</DialogTitle>
            <DialogDescription>
              {duplicatesLocked
                ? t("invoice.duplicateLockedText")
                : t("invoice.duplicateApproveText")}
              <DuplicateList duplicates={duplicateDocs} />
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              data-testid="duplicate-dialog-cancel"
              onClick={() => setDuplicateDialogOpen(false)}
            >
              {t("common.cancel")}
            </Button>
            {!duplicatesLocked && (
              <LoadingButton
                loading={approveMutation.isPending}
                data-testid="duplicate-dialog-approve"
                onClick={() => {
                  approveMutation.mutate(true)
                  setDuplicateDialogOpen(false)
                }}
              >
                {t("invoice.duplicateApproveButton")}
              </LoadingButton>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** Copies of the same document, linked so the user can compare them. */
function DuplicateList({ duplicates }: { duplicates: InvoiceDuplicateHint[] }) {
  const { t } = useTranslation()
  return (
    <ul className="mt-2 list-disc space-y-1 pl-4" data-testid="duplicate-list">
      {duplicates.map((item) => (
        <li key={item.id} data-testid={`duplicate-item-${item.id}`}>
          <RouterLink
            to="/invoices/$invoiceId"
            params={{ invoiceId: item.id }}
            className="underline underline-offset-2"
          >
            {item.original_filename}
          </RouterLink>
          {` – ${new Date(item.created_at ?? 0).toLocaleString(intlLocale())} – `}
          {t(`states.${item.state}`, { defaultValue: item.state })}
          {item.recipient_company_name
            ? ` · ${item.recipient_company_name}`
            : ""}
        </li>
      ))}
    </ul>
  )
}
