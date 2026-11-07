import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Pencil } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { CompaniesService, type RecipientCompanyPublic } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { Form, FormControl, FormItem, FormLabel } from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"
import {
  CompanyFields,
  createCompanySchema,
  fromBankAccountRows,
  toBankAccountRows,
} from "./AddCompany"

const EditCompany = ({
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

  const editSchema = createCompanySchema(t).extend({ is_active: z.boolean() })
  type EditFormData = z.infer<typeof editSchema>

  const form = useForm<EditFormData>({
    resolver: zodResolver(editSchema),
    mode: "onBlur",
    defaultValues: {
      name: company.name,
      ico: company.ico,
      dic: company.dic ?? "",
      ...fromBankAccountRows(company.bank_accounts, company.bank_account),
      address: company.address ?? "",
      note: company.note ?? "",
      is_active: company.is_active,
    },
  })

  const mutation = useMutation({
    mutationFn: (data: EditFormData) =>
      CompaniesService.updateCompany({
        path: { recipient_company_id: company.id },
        body: {
          name: data.name,
          ico: data.ico,
          dic: data.dic || null,
          address: data.address || null,
          bank_account: data.bank_account,
          bank_accounts: toBankAccountRows(data),
          note: data.note || null,
          is_active: data.is_active,
        } as never,
      }),
    onSuccess: () => {
      showSuccessToast(t("companies.updated"))
      setIsOpen(false)
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
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        <Pencil />
        {t("companies.edit")}
      </DropdownMenuItem>
      <DialogContent className="max-h-[90vh] max-w-md overflow-y-auto">
        <Form {...form}>
          <form onSubmit={form.handleSubmit((data) => mutation.mutate(data))}>
            <DialogHeader>
              <DialogTitle>{t("companies.editTitle")}</DialogTitle>
              <DialogDescription>{company.name}</DialogDescription>
            </DialogHeader>
            <CompanyFields control={form.control} />
            <FormItem className="flex items-center gap-3 pb-4">
              <FormControl>
                <Checkbox
                  checked={form.watch("is_active")}
                  onCheckedChange={(checked) =>
                    form.setValue("is_active", Boolean(checked))
                  }
                />
              </FormControl>
              <FormLabel className="font-normal">
                {t("companies.active")}
              </FormLabel>
            </FormItem>
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
              <LoadingButton type="submit" loading={mutation.isPending}>
                {t("common.save")}
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default EditCompany
