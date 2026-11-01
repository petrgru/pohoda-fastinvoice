import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"

import { type InvoicePublic, InvoicesService } from "@/client"
import {
  CompanyFields,
  type CompanyFormData,
  createCompanySchema,
  toBankAccountRows,
} from "@/components/Companies/AddCompany"
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

/**
 * "Add our company" for an invoice without an assigned recipient – prefilled
 * with the recipient detected on the document (bank accounts are the user's
 * own and are typed in manually).
 */
export function AddCompanyForInvoice({
  invoice,
  onSuccess,
  size = "default",
  variant = "outline",
  disabled = false,
}: {
  invoice: InvoicePublic
  onSuccess: () => void
  size?: "default" | "sm"
  variant?: "outline" | "ghost"
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [duplicateIco, setDuplicateIco] = useState(false)
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<CompanyFormData>({
    resolver: zodResolver(createCompanySchema(t)),
    mode: "onBlur",
    defaultValues: {
      name: invoice.recipient_name ?? "",
      ico: invoice.recipient_ico ?? "",
      dic: invoice.recipient_dic ?? "",
      address: "",
      bank_account: "",
      iban: "",
      other_accounts: [],
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
      showSuccessToast(t("addCompany.created"))
      setOpen(false)
      onSuccess()
    },
    onError: (error) => {
      if (error instanceof AxiosError && error.response?.status === 409) {
        setDuplicateIco(true)
      } else {
        handleError.call(showErrorToast, error)
      }
    },
  })

  return (
    <>
      <Button
        type="button"
        size={size}
        variant={variant}
        disabled={disabled}
        data-testid="add-company-for-invoice-button"
        onClick={() => {
          setDuplicateIco(false)
          form.reset({
            name: invoice.recipient_name ?? "",
            ico: invoice.recipient_ico ?? "",
            dic: invoice.recipient_dic ?? "",
            address: "",
            bank_account: "",
            iban: "",
            other_accounts: [],
            note: "",
          })
          setOpen(true)
        }}
      >
        <Plus className={size === "sm" ? "size-4" : "mr-2 size-4"} />
        {t("addCompany.button")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] max-w-md overflow-y-auto">
          <Form {...form}>
            <form onSubmit={form.handleSubmit((data) => mutation.mutate(data))}>
              <DialogHeader>
                <DialogTitle>{t("addCompany.title")}</DialogTitle>
                <DialogDescription>{t("addCompany.hint")}</DialogDescription>
              </DialogHeader>
              <CompanyFields control={form.control} autoLookup />
              {duplicateIco && (
                <p
                  className="pb-3 text-sm text-destructive"
                  data-testid="add-company-conflict"
                >
                  {t("addCompany.duplicateIco")}
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
                  data-testid="add-company-for-invoice-confirm"
                >
                  {t("common.create")}
                </LoadingButton>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </>
  )
}
