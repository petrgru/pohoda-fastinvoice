import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Upload } from "lucide-react"
import { useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import { InvoicesService } from "@/client"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const UploadInvoice = () => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: (selected: File) =>
      InvoicesService.uploadInvoice({ body: { file: selected } }),
    onSuccess: (response) => {
      showSuccessToast(t("upload.toastUploaded"))
      setIsOpen(false)
      setFile(null)
      // reflect the new document in every open list immediately
      queryClient.invalidateQueries({ queryKey: ["invoices"] })
      queryClient.invalidateQueries({ queryKey: ["queue"] })
      navigate({
        to: "/invoices/$invoiceId",
        params: { invoiceId: response.data.id },
      })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        setIsOpen(open)
        if (!open) setFile(null)
      }}
    >
      <Button
        onClick={() => setIsOpen(true)}
        data-testid="upload-invoice-button"
      >
        <Upload className="mr-2 size-4" />
        {t("upload.button")}
      </Button>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("upload.title")}</DialogTitle>
          <DialogDescription>{t("upload.description")}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-2 py-4">
          <Label htmlFor="invoice-file">{t("upload.file")}</Label>
          <Input
            id="invoice-file"
            data-testid="invoice-file-input"
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff"
            ref={fileInputRef}
            onInput={(e) => {
              const target = e.target as HTMLInputElement
              if (target.files?.length) {
                setFile(target.files[0])
              }
            }}
          />
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              {t("common.cancel")}
            </Button>
          </DialogClose>
          <LoadingButton
            type="button"
            loading={mutation.isPending}
            disabled={!file}
            data-testid="upload-invoice-confirm-button"
            onClick={() => file && mutation.mutate(file)}
          >
            {t("upload.upload")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default UploadInvoice
