import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { Building2 } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { type InvoicePublic, InvoicesService } from "@/client"
import {
  CompanyFields,
  type CompanyFormData,
  createCompanySchema,
  invoiceExtraAccounts,
  toBankAccountRows,
} from "@/components/Companies/AddCompany"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

/** Offers to create the parsed supplier as a new company – only after the
 * user reviews the pre-filled values and confirms. */
export function SuggestCompany({
  invoice,
  onSuccess,
}: {
  invoice: InvoicePublic
  onSuccess: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [duplicateIco, setDuplicateIco] = useState(false)
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<CompanyFormData>({
    resolver: zodResolver(createCompanySchema(t)),
    mode: "onBlur",
    defaultValues: {
      name: invoice.supplier_name ?? "",
      ico: invoice.supplier_ico ?? "",
      dic: invoice.supplier_dic ?? "",
      address: "",
      bank_account: invoice.supplier_bank_account ?? "",
      iban: invoice.supplier_iban ?? "",
      other_accounts: invoiceExtraAccounts(invoice),
      note: "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: CompanyFormData) =>
      InvoicesService.createCompanyForInvoice({
        path: { invoice_id: invoice.id },
        body: {
          name: data.name,
          ico: data.ico,
          dic: data.dic || null,
          address: data.address || null,
          bank_account: data.bank_account,
          bank_accounts: toBankAccountRows(data),
          note: data.note || null,
        },
      }),
    onSuccess: () => {
      showSuccessToast(t("suggest.created"))
      setOpen(false)
      onSuccess()
    },
    onError: (error) => {
      if (error instanceof AxiosError && error.response?.status === 409) {
        setDuplicateIco(true)
      } else {
        // surface validation / server failures instead of failing silently
        handleError.call(showErrorToast, error)
      }
    },
  })

  const supplierName =
    invoice.supplier_name ??
    (invoice.supplier_ico
      ? `${t("suggest.supplierFallback")} (IČO ${invoice.supplier_ico})`
      : t("suggest.supplierFallback"))

  return (
    <>
      <Alert data-testid="suggest-company-card">
        <Building2 className="size-4" />
        <AlertTitle>{t("suggest.title")}</AlertTitle>
        <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
          <span>{t("suggest.text", { name: supplierName })}</span>
          <Button
            size="sm"
            data-testid="suggest-company-button"
            onClick={() => {
              setDuplicateIco(false)
              form.reset({
                name: invoice.supplier_name ?? "",
                ico: invoice.supplier_ico ?? "",
                dic: invoice.supplier_dic ?? "",
                address: "",
                bank_account: invoice.supplier_bank_account ?? "",
                iban: invoice.supplier_iban ?? "",
                other_accounts: invoiceExtraAccounts(invoice),
                note: "",
              })
              setOpen(true)
            }}
          >
            {t("suggest.button")}
          </Button>
        </AlertDescription>
      </Alert>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] max-w-md overflow-y-auto">
          <Form {...form}>
            <form onSubmit={form.handleSubmit((data) => mutation.mutate(data))}>
              <DialogHeader>
                <DialogTitle>{t("suggest.button")}</DialogTitle>
                <DialogDescription>
                  {t("suggest.dialogDescription")}
                </DialogDescription>
              </DialogHeader>
              <CompanyFields control={form.control} autoLookup />
              {duplicateIco && (
                <p
                  className="pb-3 text-sm text-destructive"
                  data-testid="suggest-company-conflict"
                >
                  {t("suggest.duplicateIco")}
                </p>
              )}
              <DialogFooter>
                <DialogClose asChild>
                  <Button
                    variant="outline"
                    type="button"
                    disabled={mutation.isPending}
                  >
                    {t("common.cancel")}
                  </Button>
                </DialogClose>
                <LoadingButton
                  type="submit"
                  loading={mutation.isPending}
                  data-testid="suggest-company-confirm-button"
                >
                  {t("companies.add")}
                </LoadingButton>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </>
  )
}
