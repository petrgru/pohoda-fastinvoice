import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import {
  CompaniesService,
  type RecipientCompanyPublic,
  UtilsService,
} from "@/client"
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

const DeleteCompany = ({
  company,
  onSuccess,
}: {
  company: RecipientCompanyPublic
  onSuccess: () => void
}) => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const { data: runtime } = useQuery({
    queryKey: ["runtime-config"],
    queryFn: async () => (await UtilsService.runtimeConfig()).data,
  })

  const { mutate: deleteCompany, isPending } = useMutation({
    mutationKey: ["deleteCompany", company.id],
    mutationFn: () =>
      CompaniesService.deleteCompany({
        path: { recipient_company_id: company.id },
      }),
    onSuccess: () => {
      showSuccessToast(t("companies.deleted"))
      onSuccess()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] })
    },
  })

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuItem
        variant="destructive"
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        {t("common.delete")}
      </DropdownMenuItem>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("companies.deleteConfirmTitle")}</DialogTitle>
          <DialogDescription>
            {t("companies.deleteConfirmText", {
              name: company.name,
              ico: company.ico,
            })}
            {runtime?.develop && (
              <span className="mt-1 block text-destructive">
                {t("companies.deleteCascadeHint")}
              </span>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              {t("common.cancel")}
            </Button>
          </DialogClose>
          <LoadingButton
            onClick={() => deleteCompany()}
            loading={isPending}
            variant="destructive"
            type="button"
          >
            {t("common.delete")}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default DeleteCompany
