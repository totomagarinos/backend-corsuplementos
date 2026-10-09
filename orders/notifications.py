from django.conf import settings
from django.core.mail import send_mail


def send_new_order_email_to_admin(order):
    items_text = ""
    for item in order.items.all():
        items_text += f"- {item.quantity}x {item.variant.product.name} ({item.variant.flavor}): ${item.price}\n"

    subject = f"Nuevo pedido #{order.id} - {order.total}"

    shipping_address = getattr(order, "shipping_address", None)
    shipping_address_text = (
        f"Dirección de envío: {shipping_address}\n"
        if shipping_address
        else "Retiro en local"
    )

    message = f"""
    Tenés un nuevo pedido en la tienda!    

    DATOS DEL CLIENTE:
    Nombre: {order.customer_name}
    Email: {order.customer_email}
    Teléfono: {order.customer_phone}
    {shipping_address_text}
    
    DETALLE DEL PEDIDO:
    {items_text}

    Total a cobrar: ${order.total}
    Método de envío: {order.shipping_option}
    """

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.EMAIL_HOST_USER,
        recipient_list=[settings.EMAIL_HOST_USER],
        fail_silently=False,
    )
