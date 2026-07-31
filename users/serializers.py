from rest_framework import serializers
from django.contrib.auth.models import User
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[
            UniqueValidator(
                queryset=User.objects.all(), message="Este email ya está en uso."
            )
        ],
    )
    password = serializers.CharField(
        write_only=True, required=True, style={"input-type": "password"}, min_length=8
    )
    username = serializers.CharField(required=False, read_only=True)

    class Meta:
        model = User
        fields = ["username", "email", "password", "first_name", "last_name"]

    def create(self, validated_data):
        validated_data["username"] = validated_data["email"]
        user = User.objects.create_user(**validated_data)
        return user


class EmailTokenObtainSerializer(TokenObtainPairSerializer):
    email = serializers.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "username" in self.fields:
            del self.fields["username"]

    def validate(self, attrs):
        attrs["username"] = attrs["email"]

        return super().validate(attrs)
