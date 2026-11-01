import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { type InvoicePublic, InvoicesService } from "@/client"
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
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const DeleteInvoice = ({
  invoice,
  onSuccess,
}: {
  invoice: InvoicePublic
  onSuccess: () => void
}) => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const { mutate: deleteInvoice, isPending } = useMutation({
    mutationKey: ["deleteInvoice", invoice.id],
    mutationFn: () =>
      InvoicesService.deleteInvoice({ path: { invoice_id: invoice.id } }),
    onSuccess: () => {
      showSuccessToast(t("invoice.deleted"))
      onSuccess()
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] })
      queryClient.invalidateQueries({ queryKey: ["queue"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuItem
        data-testid="delete-invoice-menu-item"
        variant="destructive"
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        {t("invoice.deleteMenuItem")}
      </DropdownMenuItem>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("invoice.deleteConfirmTitle")}</DialogTitle>
          <DialogDescription>
            {t("invoice.deleteConfirmText", {
              filename: invoice.original_filename,
            })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              {t("common.cancel")}
            </Button>
          </DialogClose>
          <LoadingButton
            onClick={() => deleteInvoice()}
            loading={isPending}
            variant="destructive"
            type="button"
            data-testid="delete-invoice-confirm-button"
          >
            {t("common.delete")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default DeleteInvoice
