import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  Plus,
  RotateCcw,
  X,
} from "lucide-react"
import { useMemo } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { CompaniesService, InvoicesService } from "@/client"
import { AddCompanyForInvoice } from "@/components/Invoices/AddCompanyForInvoice"
import { formatPrice } from "@/components/Invoices/columns"
import { InvoiceActionsMenu } from "@/components/Invoices/InvoiceActionsMenu"
import { InvoicePreview } from "@/components/Invoices/InvoicePreview"
import { StateBadge } from "@/components/Invoices/StateBadge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
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

type FormData = {
  invoice_number?: string
  supplier_name?: string
  supplier_ico?: string
  supplier_dic?: string
  supplier_bank_account?: string
  supplier_iban?: string
  supplier_bank_accounts?: { account: string; iban: string }[]
  issue_date?: string
  due_date?: string
  total_price?: string
  total_vat?: string
  total_without_vat?: string
}

export const Route = createFileRoute("/_layout/invoices/$invoiceId")({
  component: InvoiceDetail,
  head: () => ({ meta: [{ title: "Faktura – Pohoda FastInvoice" }] }),
})

function InvoiceDetail() {
  const { t } = useTranslation()
  const { invoiceId } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const formSchema = useMemo(
    () =>
      z.object({
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
        issue_date: z.string().optional(),
        due_date: z.string().optional(),
        total_price: z.string().optional(),
        total_vat: z.string().optional(),
        total_without_vat: z.string().optional(),
      }),
    [t],
  )

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
      issue_date: invoice?.issue_date ?? "",
      due_date: invoice?.due_date ?? "",
      total_price: invoice?.total_price ?? "",
      total_vat: invoice?.total_vat ?? "",
      total_without_vat: invoice?.total_without_vat ?? "",
    },
  })

  const supplierAccounts = useFieldArray({
    control: form.control,
    name: "supplier_bank_accounts",
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] })
    queryClient.invalidateQueries({ queryKey: ["invoices"] })
    queryClient.invalidateQueries({ queryKey: ["queue"] })
  }

  const saveMutation = useMutation({
    mutationFn: (data: FormData) => {
      const body: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(data)) {
        if (key === "supplier_bank_accounts") {
          body[key] = (
            (value as FormData["supplier_bank_accounts"]) ?? []
          ).filter((row) => row.account.trim() || row.iban.trim())
          continue
        }
        if (value !== undefined && value !== "") body[key] = value
      }
      return InvoicesService.updateInvoice({
        path: { invoice_id: invoiceId },
        body: body as never,
      })
    },
    onSuccess: () => {
      showSuccessToast(t("invoice.saved"))
      invalidate()
    },
    onError: handleError.bind(showErrorToast),
  })

  const assignMutation = useMutation({
    mutationFn: (companyId: string) =>
      InvoicesService.assignCompany({
        path: { invoice_id: invoiceId },
        body: { company_id: companyId },
      }),
    onSuccess: () => {
      showSuccessToast(t("invoice.companyAssigned"))
      invalidate()
    },
    onError: handleError.bind(showErrorToast),
  })

  const approveMutation = useMutation({
    mutationFn: () =>
      InvoicesService.approveInvoice({ path: { invoice_id: invoiceId } }),
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
  const companyAccounts = (
    invoice.company?.bank_accounts?.length
      ? invoice.company.bank_accounts.map((row) => row.account ?? row.iban)
      : [invoice.company?.bank_account]
  ).filter(Boolean)
  const vatRows = invoice.vat_items ?? []
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
          <StateBadge state={invoice.state} />
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
              value={invoice.company_id ?? undefined}
              onValueChange={(value) => assignMutation.mutate(value)}
              disabled={!editable || assignMutation.isPending}
            >
              <SelectTrigger data-testid="company-select">
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
            {invoice.company && (
              <p className="text-sm text-muted-foreground">
                {t("invoice.icoLabel")}: {invoice.company.ico} ·{" "}
                {t("invoice.dicLabel")}: {invoice.company.dic ?? "—"} ·{" "}
                {companyAccounts.join(" · ")}
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
              onSubmit={form.handleSubmit((data) => saveMutation.mutate(data))}
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
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="supplier_bank_account"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t("invoice.bankAccount")}</FormLabel>
                      <FormControl>
                        <Input {...field} disabled={!editable} />
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
                        <Input {...field} disabled={!editable} />
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

              <div className="grid gap-3 rounded-md border p-3">
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
                          <FormLabel>{t("companyFields.account")}</FormLabel>
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
                              placeholder={t("companyFields.ibanPlaceholder")}
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

              {invoice.vat_check_ok === false && (
                <p className="text-sm text-destructive">
                  {t("invoice.vatMismatch", {
                    diff: formatPrice(invoice.vat_difference),
                  })}
                </p>
              )}

              {editable && (
                <LoadingButton type="submit" loading={saveMutation.isPending}>
                  {t("invoice.saveFields")}
                </LoadingButton>
              )}
            </form>
          </Form>

          {invoice.vat_items && invoice.vat_items.length > 0 && (
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
                  {invoice.vat_items.map((item) => (
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
                    onClick={() => approveMutation.mutate()}
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
          </div>
        </div>
      </div>
    </div>
  )
}
