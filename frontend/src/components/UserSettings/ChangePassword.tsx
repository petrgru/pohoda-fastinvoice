import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation } from "@tanstack/react-query"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { type UpdatePassword, UsersService } from "@/client"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

function buildSchema(t: (key: string) => string) {
  const password = z
    .string()
    .min(1, { message: t("settings.errPassRequired") })
    .min(8, { message: t("settings.errPassShort") })
  return (
    z
      .object({
        current_password: password,
        new_password: password,
        confirm_password: z
          .string()
          .min(1, { message: t("settings.errPassConfirm") }),
      })
      .refine((data) => data.new_password === data.confirm_password, {
        message: t("settings.errPassMatch"),
        path: ["confirm_password"],
      })
      // caught here too so the message is localized, not the server's English
      .refine((data) => data.new_password !== data.current_password, {
        message: t("settings.errPassSame"),
        path: ["new_password"],
      })
  )
}

type FormData = z.infer<ReturnType<typeof buildSchema>>

const ChangePassword = () => {
  const { t } = useTranslation()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const formSchema = useMemo(() => buildSchema(t), [t])
  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onSubmit",
    criteriaMode: "all",
    defaultValues: {
      current_password: "",
      new_password: "",
      confirm_password: "",
    },
  })

  const mutation = useMutation({
    mutationFn: (data: UpdatePassword) =>
      UsersService.updatePasswordMe({ body: data }),
    onSuccess: () => {
      showSuccessToast(t("settings.passwordUpdated"))
      form.reset()
    },
    onError: handleError.bind(showErrorToast),
  })

  const onSubmit = async (data: FormData) => {
    mutation.mutate(data)
  }

  return (
    <div className="max-w-md">
      <h3 className="py-4 text-lg font-semibold">
        {t("settings.changePassword")}
      </h3>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-4"
        >
          <FormField
            control={form.control}
            name="current_password"
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>{t("settings.currentPassword")}</FormLabel>
                <FormControl>
                  <PasswordInput
                    data-testid="current-password-input"
                    placeholder="••••••••"
                    aria-invalid={fieldState.invalid}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="new_password"
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>{t("settings.newPassword")}</FormLabel>
                <FormControl>
                  <PasswordInput
                    data-testid="new-password-input"
                    placeholder="••••••••"
                    aria-invalid={fieldState.invalid}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="confirm_password"
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>{t("settings.confirmPassword")}</FormLabel>
                <FormControl>
                  <PasswordInput
                    data-testid="confirm-password-input"
                    placeholder="••••••••"
                    aria-invalid={fieldState.invalid}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <LoadingButton
            type="submit"
            loading={mutation.isPending}
            className="self-start"
            data-testid="password-update-button"
          >
            {t("settings.updatePassword")}
          </LoadingButton>
        </form>
      </Form>
    </div>
  )
}

export default ChangePassword
